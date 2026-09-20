The pluggins are the usage of the plugin system in the AI Personal Agent. They are designed to extend the capabilities of the agent by providing additional functionalities. Each plugin is a self-contained module that can be easily integrated into the agent's architecture.

### Manifest

The manifest is declarative metadata:

What is this plugin?
What capabilities does it provide?
What permissions does it require?
What parameters does each capability accept?
What configuration does it support?
What Python class implements it?


Python implementation

The .py file contains the actual behavior:

browser.navigate     → Playwright
browser.click        → Playwright
planner.decompose    → planning logic / LLM
task_manager.add     → persistence


Python implementation

manifest.json
      │
      │ describes
      ▼
Plugin
      │
      │ implemented by
      ▼
plugin.py

We could eventually have a completely declarative plugin where a generic runtime executes everything from the manifest.