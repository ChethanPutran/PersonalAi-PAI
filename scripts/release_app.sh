# bump to a new version
python scripts/bump_plugin.py browser patch --commit
# commits plugin_registry/browser/1.0.1/ with message "browser: 1.0.0 → 1.0.1"

# push
git push