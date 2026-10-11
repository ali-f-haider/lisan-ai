"""Sentence cuts for new source languages using existing word timestamps."""
import lang_text


def split_timed_row(row, source_language, max_duration=15.0):
    words = row.get('words') or []
    if len(words) < 2:
        return [row]
    result, current = [], []

    def emit():
        item = dict(row)
        item.update(words=list(current), text=' '.join(str(w.get('word') or '').strip() for w in current).strip(),
                    start=round(float(current[0]['start']), 2), end=round(float(current[-1]['end']), 2))
        result.append(item)

    for word in words:
        if current:
            text = ' '.join(str(w.get('word') or '').strip() for w in current)
            if lang_text.ends_sentence(text, source_language, profile='tidy') or float(word['end']) - float(current[0]['start']) > max_duration:
                emit()
                current = []
        current.append(word)
    if current:
        emit()
    return result
