# Contributing to Dirracuda

## Development Setup

1. Run the setup script: `./setup_gui_dev_env.sh`
2. Activate environment: `source gui_env/bin/activate`
3. Read `docs/DEVNOTES.md` for architecture and patterns

## Documentation Standards

- **Document as you code** - Update DEVNOTES.md during each session
- **Complete docstrings** for all functions and classes
- **Architecture decisions** must be documented with reasoning
- **Testing results** must be recorded in DEVNOTES.md

## Testing Requirements

- All GUI components must have corresponding tests
- Test with mock data before live backend integration
- Cross-platform testing required for contributions

## Pull Request Process

1. Update DEVNOTES.md with development session notes
2. Ensure all tests pass with `pytest tests/`
3. Update USER_GUIDE.md for user-facing changes
4. Add changelog entry to CHANGELOG.md

## Code Standards

- Follow PEP 8 style guidelines
- Use type hints where appropriate
- Include comprehensive docstrings with design decisions
- Handle errors gracefully with user-friendly messages
