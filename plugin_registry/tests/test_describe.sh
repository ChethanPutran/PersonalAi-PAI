cd /home/chethan/Desktop/Chethan/aiml_projects/advanced_projects/personal_ai/ai_personal_agent

for so in plugin_registry/*/1.0.0/linux/libpai_*.so; do
  echo "=== $so ==="
  python3 tools/pai_test.py "$so" --describe
done