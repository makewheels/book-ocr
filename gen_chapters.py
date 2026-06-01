"""从 book.json 生成按章拆分的增强 Markdown。
用法: uv run gen_chapters.py out/某书.json [输出目录]
"""
import json, sys, os, re
from pathlib import Path

EMOJI = {
    "恋爱场景": "📋",
    "一般回答": "🥱",
    "高情商回答": "💡",
    "恋爱锦囊": "🧠",
}

def slug(text):
    return re.sub(r"[^\w一-鿿]+", "", (text or "未命名").strip())[:20]

def block(k, v):
    e = EMOJI.get(k, "▸")
    return f"**{e} {k}**\n\n{v.strip()}"

def render_chapter(ch: dict) -> str:
    head = ch.get("title") or ch.get("no") or ""
    lines = [f"# {head}", ""]
    for i, sec in enumerate(ch["sections"]):
        if i > 0:
            lines += ["---", ""]
        t = sec.get("title") or "(无标题)"
        pg = f" · P{sec['printed_page']}" if sec.get("printed_page") else ""
        lines += [f"## {t}{pg}", ""]
        for k, v in (sec.get("blocks") or {}).items():
            lines += [block(k, v), ""]
    return "\n".join(lines).rstrip() + "\n"

def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("out/一秒心动.json")
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else src.parent / "chapters"
    out_dir.mkdir(exist_ok=True)
    book = json.loads(src.read_text())

    index = [f"# {book['title']}", "", f"> {book['author']} 编著", ""]
    for ch in book["chapters"]:
        ch_no = ch.get("no") or "未命名"
        ch_title = ch.get("title") or ""
        s = slug(ch_title or ch_no)
        CN = {"一":"1","二":"2","三":"3","四":"4","五":"5","六":"6","七":"7","八":"8","九":"9","十":"10"}
        m = re.search(r"第(\S+)章", ch_no or "")
        if m:
            raw = m.group(1)
            seq = str(CN.get(raw, raw)) if len(raw)==1 else str(10+CN.get(raw[1],raw[1]))
        else:
            seq = "0"
        fname = f"{int(seq):02d}_{s}.md"
        index.append(f"- [{ch_title}](chapters/{fname})")
        (out_dir / fname).write_text(render_chapter(ch))

    index_path = out_dir / "README.md"
    index_path.write_text("\n".join(index).rstrip() + "\n")
    print(f"生成 {len(book['chapters'])} 章 + 索引 → {out_dir}/")

if __name__ == "__main__":
    main()
