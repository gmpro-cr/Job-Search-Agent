import re

with open("templates/preferences.html", "r") as f:
    content = f.read()

# Pattern for div wrapping label, optional textarea/input, and p text-gray-400 mt-1
# This is tricky because the inputs could be multi-line.
# Let's write a targeted function to find inputs and their trailing helper text.

fixes = [
    (r'(<textarea[^>]*id="job_titles"[^>]*>.*?</textarea>)\s*(<p class="text-xs text-gray-400 mt-1">.*?</p>)', r'\2\n            \1'),
    (r'(<textarea[^>]*id="locations"[^>]*>.*?</textarea>)\s*(<p class="text-xs text-gray-400 mt-1">.*?</p>)', r'\2\n            \1'),
    (r'(<textarea[^>]*id="industries"[^>]*>.*?</textarea>)\s*(<p class="text-xs text-gray-400 mt-1">.*?</p>)', r'\2\n            \1'),
    (r'(<textarea[^>]*id="transferable_skills"[^>]*>.*?</textarea>)\s*(<p class="text-xs text-gray-400 mt-1">.*?</p>)', r'\2\n            \1'),
    (r'(<input[^>]*id="digest_time"[^>]*>)\s*(<p class="text-xs text-gray-400 mt-1">.*?</p>)', r'\2\n            \1'),
    (r'(<input[^>]*id="email"[^>]*>)\s*(<p class="text-xs text-gray-400 mt-1">.*?</p>)', r'\2\n            \1'),
    (r'(<input[^>]*id="gmail_address"[^>]*>)\s*(<p class="text-xs text-gray-400 mt-1">.*?</p>)', r'\2\n            \1'),
    (r'({% endif %})\s*(<p class="text-xs text-gray-400 mt-1">\s*Generate at.*?</p>)', r'\2\n                \1'),
    (r'(<input[^>]*id="agent_score_threshold"[^>]*>)\s*(<p class="text-xs text-gray-400 mt-1">.*?</p>)', r'\2\n            \1'),
    (r'(<input[^>]*id="agent_host"[^>]*>)\s*(<p class="text-xs text-gray-400 mt-1">.*?</p>)', r'\2\n            \1'),
    (r'({% endif %})\s*(<p class="text-xs text-gray-400 mt-1">\s*Get from.*?</p>)', r'\2\n                \1'),
    (r'(<input[^>]*id="telegram_chat_id"[^>]*>)\s*(<p class="text-xs text-gray-400 mt-1">\s*Find your ID.*?</p>)', r'\2\n                \1'),
    (r'(<input[^>]*id="telegram_min_score"[^>]*>)\s*(<p class="text-xs text-gray-400 mt-1">.*?</p>)', r'\2\n            \1'),
    (r'({% endif %})\s*(<p class="text-xs text-gray-400 mt-1">When set, the scraper.*?</p>)', r'\2\n            \1'),
    (r'({% endif %})\s*(<p class="text-xs text-gray-400 mt-1">Stored securely. Leave blank.*?</p>)', r'\2\n                \1')
]

for pat, repl in fixes:
    content = re.sub(pat, repl, content, flags=re.DOTALL)

# Adjust mt-1 to mb-2 for relocated helper texts
content = re.sub(r'class="text-xs text-gray-400 mt-1"', 'class="text-xs text-gray-400 mb-2 mt-1"', content)

with open("templates/preferences.html", "w") as f:
    f.write(content)
