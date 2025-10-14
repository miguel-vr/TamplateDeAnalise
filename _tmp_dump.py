from pathlib import Path
text = Path('core/gpt_core.py').read_text(encoding='utf-8')
start = text.index('    def _chat_completion(')
end = start
while True:
    if text.startswith('\n', end):
        # check next non-empty line begins with 'def ' meaning function ended
        remaining = text[end+1:]
        if remaining.startswith('\n') or remaining.startswith('def _offline_analysis') or remaining.startswith('    #'):
            break
    end += 1
old_block = text[start:end]
print(repr(old_block))
print('---BLOCK END---')
print(old_block)
