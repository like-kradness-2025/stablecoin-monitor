
import re

with open(stablecoin_monitor.py, r) as f:
    content = f.read()

# Fix both occurrences of the malformed formatter
content = content.replace(
    "ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, pos: f{x:.4f}))",
    "ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, pos: f{x:.4f}))"
)

with open(stablecoin_monitor.py, w) as f:
    f.write(content)

print("Fixed")
