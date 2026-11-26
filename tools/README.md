# tools

One `<tool>.yaml` file per tool. Keeping each tool in its own file makes it easy to compare setups and open pull requests without merge conflicts.

Each file lists the tool's name, its list price (`pricing`: per call, per result, both, or none) with a link to where the price comes from, and its setups. The runner tries every setup for each seed and keeps the best one.

To add or update a tool: edit `tools/<tool>.yaml`, run the tests, and open a pull request. A new API also needs `harness/adapters/<tool>.py` and one line in `harness/adapters/__init__.py`.
