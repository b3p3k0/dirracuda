Implementing the “Pry” Weak Password Audit Function in SMBSeek
Overview

MVP decisions (2026-01-03):
- Single host + single username per run; no spray/multi-user yet.
- Stream wordlists line-by-line; block .gz inputs; skip empty lines and >256-char entries.
- Default delay between attempts: 1.0s; max_attempts configurable (0 = unlimited).
- Lockout detection counts as failed run; stop when detected by default.
- Progress via status text (“Pry <ip>: tried X/Y passwords…”), no progress bar.
- Do not store found credentials; show only in batch summary.
- Missing/unreadable wordlist or missing username blocks start with a friendly dialog.
- Recommended wordlist: download from https://github.com/danielmiessler/SecLists (e.g., `rockyou.txt`) and set `pry.wordlist_path` accordingly (default points to `conf/wordlists/rockyou.txt` if you place it there).

We plan to integrate a new “Pry” function into SMBSeek to audit weak passwords on a selected SMB host. This feature will leverage the capabilities of the open-source mmcbrute tool (BSD-3 licensed) to perform SMB brute-force credential testing
github.com
github.com
. The goal is to allow a security auditor to select a discovered host and attempt to authenticate with a given username against a list of common passwords. This proactive audit can reveal weak credentials (e.g. accounts named after a share with guessable passwords) and help demonstrate the importance of strong password hygiene to clients. Importantly, we will credit mmcbrute in our documentation and code comments as the inspiration for this feature, per the CEO’s directive.

Integration Approach

To incorporate this functionality cleanly, we’ll follow the existing SMBSeek architecture:

New UI Button: Add a “🔓 Pry Selected” button to the main server list GUI (xSMBSeek) alongside Probe, Extract, and Browse. This button will be enabled only when a single host is selected (and no other batch job is running), similar to how “Browse (read-only)” is restricted to one host
GitHub
. Clicking it triggers a prompt for credentials.

Pry Dialog Prompt: On activation, open a modal dialog to collect:

Username to test: (Text field input)

Password wordlist: (File path input with a “Browse…” button to choose a wordlist file from disk)

Options: Checkboxes for additional behaviors:

“Use username as password” (try the username itself as a password once, default enabled based on config)

“Stop on account lockout” (default enabled – to avoid locking accounts unless overridden)

(Optional) “Verbose output (show failures)” – default off for MVP, since initially we only report successes.

We may omit a domain field for MVP; if needed, users can specify a domain by prefixing the username (e.g. DOMAIN\user). The brute logic will parse it if present.

A Start button to begin the audit, and Cancel to abort. The dialog ensures a wordlist file is selected (or provides an error if not).

Background Brute-Force Process: Once the user clicks Start, the dialog will close (or disable inputs) and the brute-force run will commence in a background thread (to keep the UI responsive). We will reuse SMBSeek’s batch job framework for consistency:

We’ll introduce a new batch job type, e.g. job_type="pry", handled similarly to existing “probe” and “extract” jobs. Specifically, in ServerListWindow._start_batch_job, if job_type == "pry", it will spawn the task and manage it in the thread pool (with a fixed worker count of 1 for a single-target brute force).

A new method ServerListWindow._execute_pry_target(target, options, cancel_event) will be implemented, analogous to _execute_probe_target and_execute_extract_target
GitHub
GitHub
. This method will perform the actual password attempts using the mmcbrute approach.

Brute-Force Logic (mmcbrute-inspired): The Pry implementation will use the Impacket library (as mmcbrute does) to attempt an SMB authentication for each candidate password:

Load Credentials: Retrieve the target IP and the username from the input. Read the password list from the specified wordlist file into memory (or stream it line by line to handle large lists). If the “user as pass” option is enabled, prepend the username itself as the first password attempt (common weak credential).

Attempt Logins: For each password candidate, attempt an SMB connection to the target with the given username and password. We’ll leverage Impacket’s SMB connection (or a similar mechanism) to test credentials. The mmcbrute technique relies on SMB error codes to detect success or failure
github.com
. A valid credential yields a different error code (or no error) versus an invalid password. We must also detect specific error codes:

Success: If authentication succeeds (or we receive an SMB status indicating login OK), record the success.

Failure: If credentials are wrong, continue to next password. By default, we will not log each failure to the UI to avoid clutter (consistent with mmcbrute’s default silent-fail behavior
github.com
).

Account Lockout: If we encounter an error code indicating the account is locked (e.g. STATUS_ACCOUNT_LOCKED_OUT), stop further attempts. By default, Pry will cease attempts upon lockout detection to avoid causing disruption to the client’s environment. (This corresponds to mmcbrute’s default behavior, which can be overridden by “honey badger mode”
github.com
.) If the “Stop on lockout” option is checked (default), we will break out and flag this event. If the user unchecks it (enabling a “honey badger” mode), Pry will ignore lockout errors and continue (use with caution).

Cancellation: If the user hits the global Stop Batch button during the run, the cancel_event will be set and our loop will break cleanly (exiting early just as Probe/Extract do).

Timing/Delay: To reduce load and avoid tripping intrusion detection, we may insert a short delay between attempts. A reasonable default (e.g. 0.5 seconds) can be configurable (see Config Defaults below). This prevents sending hundreds of attempts per second and mimics a more realistic password spray pace.

Progress Feedback: We will provide basic feedback during the brute-force:

Status Bar: The main window’s status label can show progress like “Pry batch (30/100) passwords tried…” updating every few attempts. mmcbrute emphasizes a real-time progress bar to avoid frustration
github.com
, so we should at least update the UI periodically. Alternatively or additionally, we can include a progress indicator in the Pry dialog itself (e.g. a progress bar widget or text percentage).

Because we only report success at the end for MVP, detailed progress readout is optional. However, showing some progress or a spinner is important for good UX. We might update the status every N attempts or each X% of the list.

Result Handling: When the list is exhausted or a success found (or lockout occurs), the thread returns a result dict. We will fill the result similar to other batch jobs:

status: "success" (the job ran to completion – note that even if no password was found, the operation itself is successful; a lockout might be treated as a controlled “failed” status with note).

notes: a human-readable summary:

If a password was found: e.g. “user joe authenticated with Password123”.

If none worked: e.g. “user joe not authenticated with given wordlist” (meaning no password in the list succeeded).

If stopped due to lockout: e.g. “user joe account locked – stopped after 15 attempts”.
(These messages will be shown in the batch summary dialog after completion.)

The result is appended to self.batch_job["results"] and the normal batch completion flow will finalize, re-enabling UI controls and showing a summary popup
GitHub
GitHub
. For MVP, the summary can be a simple info box listing the outcome message for the host. We can refine this UX later (e.g. color-coding success vs failure lines in the summary).

User Interface Design

Main Window Changes:
We will introduce a new button in the server list window’s action button panel:

[🔍 Probe Selected] [📦 Extract Selected] [🗂️ Browse (read-only)] **[🔓 Pry Selected]** [⏹ Stop Batch] [📋 View Details] ...

Placement & Style: The “Pry Selected” button will be added to the right-side button container in ServerListWindow._create_button_panel() after the Browse button (before the Stop button, for example). It will likely use a secondary style (similar to Extract/Browse) unless we want to highlight it. The text could be “🔓 Pry Selected” (using a padlock emoji to imply cracking a lock) or a similar icon to convey password testing.

Enabled/Disabled Logic: Like the other buttons, it should be disabled by default and only enabled when a valid selection is made and no batch job is running
GitHub
. We will include self.pry_button in the_update_action_buttons_state logic alongside probe/extract/browse so that:

If one or more rows are selected and no batch is active: Pry button = enabled (though we will still enforce in code that only one host can be processed at once).

If no selection or a batch job is ongoing: Pry button = disabled.

Event Binding: Clicking the Pry button triggers a new handler ServerListWindow._on_pry_selected(). This will:

Hide any open context menu (like other handlers do via self._hide_context_menu()).

If a batch job is running, show an info dialog “Please wait for the current batch to finish or stop it before running Pry.” (to prevent overlapping operations)
GitHub
.

Use our selection logic: ensure exactly one server is selected. If not, show a warning: “Select one server to run a password audit.” and abort (similar to the browse function’s single-selection requirement
GitHub
).

Call our Pry dialog to get user inputs (username, wordlist, options). If the user cancels or closes the dialog, do nothing further.

If inputs are provided, build the target descriptor for the selected host (e.g. using _build_selected_targets() which returns a list of server info dicts). We’ll take the first element and augment it with the username (and maybe domain) to test. Alternatively, we can pass the username/password list via the options dict rather than altering the target.

Invoke self._start_batch_job("pry", targets=[target], options=pry_options_dict). The options will include things like the password list or path, and boolean flags for user_as_pass, stop_on_lockout, etc. We also explicitly set worker_count=1 for this job.

Pry Dialog Mockup:
To ensure the UI/UX is clear, here’s a sketch of the dialog that appears when clicking Pry:

+---------------------------------------+

|   🔓 **Pry Weak Password Audit**       |
| ------------------------------------- |
| Target Host: 10.0.0.45 (example.com)   |
|                                       |
| Username to test: [______________]    |
| Password wordlist: [______________] 📁 |
|                                       |
| Options: [✔] Try username as password |
|          [✔] Stop on account lockout  |
|          [ ] Verbose output (log fails) |
|                                       |
|                (Cancel)   (Start)     |
+---------------------------------------+

The dialog title clearly labels the function (perhaps “Pry – Weak Password Test”). We might show the target host/IP in the dialog for confirmation (as in Target Host: X).

Username to test: a text entry. We’ll validate that it’s non-empty when Start is clicked. (Possibly we could pre-fill this field if, for example, the selected server has a share name that looks like a person’s name – but that’s an enhancement idea for later.)

Password wordlist: an entry field for the file path, with a “📁” Browse button that opens a file picker dialog (using tkinter.filedialog.askopenfilename) to select a text file containing passwords. After selection, the path is shown in the entry. We ensure this file exists and is readable.

Options: Three checkboxes as described. Defaults loaded from config (discussed in next section):

“Try username as password” – on by default (since it’s low-hanging fruit to catch accounts with identical username/password).

“Stop on account lockout” – on by default (prevent lockouts).

“Verbose output” – off by default (MVP doesn’t show each failed attempt).

Buttons: Start begins the process (after validating inputs), and Cancel closes the dialog without starting anything.

This dialog’s layout can be implemented in tkinter similarly to how the “Batch Probe Settings” dialog is built in code
GitHub
GitHub
. We will position labels and fields in a grid or pack layout. Upon hitting Start, if any required field is missing (e.g. no username or no wordlist file), we show an error message (e.g. a messagebox.showerror prompting the user to fill in all fields) and do not proceed. Otherwise, we close the dialog and return the collected inputs.

Configuration Defaults and Options

We will introduce new config settings (in conf/config.json and the example config) to allow users and admins to adjust the behavior of Pry. These settings provide sane defaults while letting advanced users tweak as needed:

pry.wordlist_path (string, default empty): Default path to a wordlist file to use for Pry. If set, the Pry dialog can pre-populate the wordlist field with this path (still allowing override). By default this is blank, meaning the user must choose a file each time (since the CEO prefers not bundling a wordlist and to let users supply their own).

pry.user_as_pass (boolean, default true): Whether to attempt the username as the password as an initial attempt. This catches cases like user "alice" with password "alice". Users can uncheck this in the UI per run; this config just sets the default state of that checkbox.

pry.stop_on_lockout (boolean, default true): Whether to cease attempts if an account lockout is detected. Default true for safety. If set false (or if user unchecks the box), the tool will continue attempting passwords even after a lockout error (mimicking mmcbrute’s “-b honey badger” mode
github.com
). We will clearly log if a lockout was detected either way.

pry.verbose (boolean, default false): Controls whether every failed attempt is logged/shown. By default, false – meaning we only report the final outcome or successes, to keep output clean
github.com
. If true, we might log each attempt to console or a debug log window. (For the GUI, perhaps a scrolling log text in the Pry dialog could show attempts if verbose is true – this could be a future enhancement, not necessarily in MVP UI.)

pry.attempt_delay (float, default 0.5): Delay in seconds between password attempts. Default half a second to throttle brute-force speed. Users can reduce this if they have permission and need speed, or increase it to be more stealthy. In high-security environments with strict lockout policies, a slower attempt rate reduces the chance of rapid lockouts. (We will sleep for this duration between attempts if set > 0.)

pry.max_attempts (int, default maybe 0 meaning no limit aside from wordlist length): This could optionally set an upper bound of attempts per run, to avoid extremely large wordlists running forever or causing too many lockouts. For MVP, we might omit this and assume the user-provided wordlist is of reasonable size or that they will cancel if it’s too long. It can be added if needed.

These defaults will reside in the config file so they can be organization-specific. We’ll load them in the GUI via the settings_manager similar to how probe/extract defaults are loaded
GitHub
. For example, when opening the Pry dialog, we call something like:

default_user_as_pass = self.settings_manager.get_setting('pry.user_as_pass', True)

and use that to initialize the checkbox variable.

Future Expansion Ideas

While the MVP focuses on a single-user, single-host password audit, there are several ways we can expand the Pry functionality in later iterations:

Multiple Username Testing: Allow the user to supply not just one username but a list of usernames (or point to a username list file, similar to mmcbrute’s original CLI). The tool could then attempt each username with the given passwords. This starts bordering on a broader brute-force audit, so we’d need to carefully present results (e.g. which username succeeded with which password). For the GUI, this might be implemented as accepting a CSV of “username,password” or two files. In a simple expansion, we could permit a comma-separated list of usernames in the input field and iterate through each.

Password Spraying Mode: Expand Pry to support multi-host or multi-user “spray” attacks in a controlled way. For example, if the same user exists on many selected hosts (common in domain environments) or if we want to try a single common password across all selected servers. This could help identify a single weak credential that grants access to many systems (e.g. a default password “Winter2025!” used everywhere). Implementation-wise, this means allowing multiple targets per Pry job and iterating appropriately, with careful timing to avoid lockouts (try one password on all hosts before moving to the next password, etc.). This is a more advanced scenario and might require a different UI flow (perhaps a separate “Spray” function).

Auto-Username Suggestions: As noted, our analysis often finds share names that look like people’s names (e.g. a share named “/JOHN_DOCS” suggests a user John). We can automate hinting the user to test those. For example, the GUI could list any person-like share names for the selected host and offer them as quick-select usernames in the Pry dialog (perhaps a dropdown or suggestion list). This would streamline the auditor’s job – they might simply pick “john” from a suggestions dropdown and proceed. In future, we could even auto-run a very small password list (like ["<username>", "<username>123", "Password123", "Welcome1"]) against such detected names as part of the probe phase, but that should be opt-in to avoid aggressive actions.

Integration with Discovery/Probe Results: If the SMB server is part of a Windows domain and we have insight (e.g. via SMB banners or LDAP) into domain name or user lists, we could incorporate that. For instance, if we know the domain, we might automatically include the domain in the authentication attempt (currently we assume either local or let user specify DOMAIN\user). A future enhancement is to allow specifying the domain in the Pry dialog or auto-detecting it.

Result Utilization: Once a weak credential is found, SMBSeek could automatically use it to fetch more data:

For example, upon a successful login, we could immediately list accessible shares with those creds (since now this host might have previously “No accessible shares” as guest, but with valid user creds there could be accessible ones). We could prompt the user “Credentials found! Would you like to browse shares as this user now?” and then open the Browse window with those creds. This tight integration would demonstrate the impact of the cracked password (showing what an attacker could do with it). For MVP, we’ll simply report the success, but logging the found credential and perhaps storing it (securely) in the database for that host could be very useful. We should at least mark in the DB that host X had a weak credential found, and avoid storing the plaintext password unless necessary (maybe store a hash or just a flag). Documentation and client policy will dictate how to handle discovered passwords.

Logging and Reporting: Extend the reporting to include weak credential findings. For example, in summary reports or export data, have a field indicating if weak passwords were identified. We will definitely document in release notes that this feature was added, and how it was inspired by mmcbrute (ensuring we attribute the original author and repository in our docs and credits).

Each of these expansions can be added incrementally, keeping the initial implementation simple and focused on the core use-case (single host, single user, user-provided wordlist).

Attribution and License Compliance

Since we are using mmcbrute’s approach (and potentially some of its code) as a foundation, we must ensure proper attribution:

Code Comments: Any functions or logic adapted from mmcbrute will include header comments crediting the mmcbrute project (e.g. “# Derived from mmcbrute by Gilks (BSD-3-Clause Licensed)” along with a URL to the source).

Documentation: We will add a note in the SMBSeek README or User Guide’s credits section acknowledging that the Pry feature is inspired by and partially based on mmcbrute
github.com
, which is BSD-3 licensed. The exact wording could be: “The password auditing functionality (Pry) is inspired by the mmcbrute tool
github.com
. We thank its author for the original implementation. SMBSeek incorporates similar techniques under the terms of the BSD-3-Clause license.” This satisfies our CEO’s requirement to “always give props where due.”

License File: If we include any significant portion of mmcbrute code, the BSD-3 license requires us to retain its license notice. We should include the mmcbrute LICENSE file (or at least the text of the BSD-3 license and a notice) in our repository, or in a NOTICE file, to comply with terms
github.com
. Given that our project is MIT-licensed, including a BSD-3 licensed component is compatible, but we need to make sure we don’t mix code in a way that violates either license. We likely can have the mmcbrute-derived module clearly marked with the BSD license.

By doing this, we not only comply legally but also maintain goodwill and transparency, which aligns with our values.
