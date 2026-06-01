"""把图片版/扫描版 PDF 书籍逐页用通义千问 VLM 识别成结构化 JSON。

通用工具:适用于以「章 → 小节」组织的中文图书。

用法:
  uv run pdf2json.py --pdf ~/Downloads/某本书.pdf --test 0 2 10 40 60   # 抽几页验证(0基页号)
  uv run pdf2json.py --pdf ~/Downloads/某本书.pdf                       # 跑全书 → out/<书名>.json
  uv run pdf2json.py --pdf book.pdf --title 一秒心动 --author 李澈 \
      --hint "恋爱沟通话术书,正文页有【恋爱场景】【一般回答】【高情商回答】【恋爱锦囊】等小标题"

API Key 从环境变量或本目录 .env 读取(DASHSCOPE_API_KEY),不写进代码、不提交 git。
"""
import argparse
import base64
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import fitz
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

ROOT = Path(__file__).parent
MODEL = os.getenv("MODEL", "qwen3-vl-plus")

PROMPT_TMPL = """你是中文图书 OCR 与版面解析助手。这是一本{book_desc}的某一页扫描图。
请准确识别页面文字并判断页面类型,只输出一个 JSON 对象,不要任何解释、不要 markdown 代码块。{hint}

字段:
- page_type: 取值之一,按以下优先级判断
    "toc"(目录):页面含多行「条目标题 …… 页码数字」的列表(常以空格或 / 接数字结尾);
        即使页面顶部有「第X章」黑底标题条,只要下方是这种条目列表,也一律判为 toc。
    "chapter"(章/部分 分隔页):页面中央有「第X章」方块徽标加章标题、且没有正文小节段落;
        注意徽标里的「第X章」可能竖排,务必识别并填入 chapter_no。
    "cover"(封面) / "preface"(序言前言:整页是连续散文段落、无小节标题且位于书籍开头,
        序言可跨多页,承接的段落页也算 preface) /
    "section"(带独立小节标题、且有正文内容的正文页) /
    "content"(承接上一节、没有独立标题的普通正文页) /
    "blank"(空白或无正文)
- printed_page: 页面上印刷的页码数字(整数);没有则 null
- chapter_no: 仅 chapter 页填,如 "第三章";否则 null
- chapter_title: 仅 chapter 页填,章标题文字;否则 null
- section_title: 仅 section 页填,该页的小节/话题标题;否则 null
- blocks: 仅 section/content 页填,对象,键为页面中【】内的小标题(若有),值为其下完整正文;
          若正文没有【】小标题,则用 {{"正文": "整段文字"}};否则 {{}}
- raw_text: toc/preface/cover/blank 页填该页全部可读文字(保留换行);其它类型填 ""

要求:逐字准确,不漏字、不脑补;括号用中文括号;保留原文标点。"""


def build_prompt(book_desc: str, hint: str) -> str:
    return PROMPT_TMPL.format(
        book_desc=book_desc or "中文图书",
        hint=f"\n补充背景:{hint}" if hint else "",
    )


def make_client() -> OpenAI:
    return OpenAI(
        api_key=os.environ["DASHSCOPE_API_KEY"],
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )


def render_page(doc, idx: int, pages_dir: Path, zoom: float = 2.2) -> Path:
    out = pages_dir / f"p{idx:03d}.png"
    if not out.exists():
        doc[idx].get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(out)
    return out


def _strip_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def ocr_page(doc, idx, client, prompt, pages_dir, cache_dir, use_cache=True) -> dict:
    cache = cache_dir / f"p{idx:03d}.json"
    if use_cache and cache.exists():
        return json.loads(cache.read_text())
    img = render_page(doc, idx, pages_dir)
    b64 = base64.b64encode(img.read_bytes()).decode()
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    raw = _strip_json(resp.choices[0].message.content)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"page_type": "error", "raw_text": raw}
    data["pdf_page"] = idx
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return data


def assemble(pages: list[dict], title: str, author: str) -> dict:
    book = {"title": title, "author": author, "chapters": [], "front_matter": []}
    cur_ch = None
    cur_sec = None
    for p in sorted(pages, key=lambda x: x["pdf_page"]):
        t = p.get("page_type")
        if t == "chapter":
            cur_ch = {"no": p.get("chapter_no"), "title": p.get("chapter_title"),
                      "pdf_page": p["pdf_page"], "sections": []}
            book["chapters"].append(cur_ch)
            cur_sec = None
        elif t == "section":
            if cur_ch is None:
                # 章节开始前的 section 实为前置内容(如序言续页),不可能是真正小节
                txt = "\n".join(x for x in [p.get("section_title"), *(p.get("blocks") or {}).values()] if x)
                book["front_matter"].append({"page_type": "preface", "pdf_page": p["pdf_page"], "text": txt})
                continue
            cur_sec = {"title": p.get("section_title"), "printed_page": p.get("printed_page"),
                       "pdf_page": p["pdf_page"], "blocks": p.get("blocks") or {}}
            cur_ch["sections"].append(cur_sec)
        elif t == "content":
            blocks = p.get("blocks") or {}
            if cur_sec is not None:
                for k, v in blocks.items():
                    cur_sec["blocks"][k] = (cur_sec["blocks"].get(k, "") + "\n" + v).strip() \
                        if k in cur_sec["blocks"] else v
            elif cur_ch is not None:
                cur_ch.setdefault("loose_content", []).append(blocks)
        elif t in ("preface", "toc", "cover"):
            book["front_matter"].append({"page_type": t, "pdf_page": p["pdf_page"],
                                         "text": p.get("raw_text", "")})
    return book


def to_markdown(book: dict) -> str:
    lines = [f"# {book['title']}", ""]
    if book.get("author"):
        lines += [f"> {book['author']} 编著", ""]
    pref = [fm["text"] for fm in book.get("front_matter", [])
            if fm["page_type"] == "preface" and fm.get("text")]
    if pref:
        lines += ["## 序言", "", "\n\n".join(pref), ""]
    for ch in book["chapters"]:
        head = " ".join(x for x in (ch.get("no"), ch.get("title")) if x)
        if head:
            lines += [f"## {head}", ""]
        for sec in ch["sections"]:
            t = sec.get("title") or "(无标题)"
            pg = f" (P{sec['printed_page']})" if sec.get("printed_page") else ""
            lines += [f"### {t}{pg}", ""]
            for k, v in (sec.get("blocks") or {}).items():
                lines += [f"**{k}**", "", v, ""]
    return "\n".join(lines).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, help="PDF 文件路径")
    ap.add_argument("--title", default=None, help="书名(默认取文件名)")
    ap.add_argument("--author", default="", help="作者")
    ap.add_argument("--desc", default="中文图书", help="书籍类型描述,用于提示模型")
    ap.add_argument("--hint", default="", help="版面/字段的补充提示,提升识别准确度")
    ap.add_argument("--test", nargs="*", type=int, help="只跑这些页(0基),打印结果不写文件")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    pdf_path = Path(args.pdf).expanduser()
    title = args.title or pdf_path.stem
    slug = re.sub(r"[^\w一-鿿-]+", "_", title).strip("_")
    pages_dir = ROOT / "pages" / slug
    cache_dir = ROOT / "out" / "cache" / slug
    pages_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    client = make_client()
    prompt = build_prompt(args.desc, args.hint)
    use_cache = not args.no_cache

    if args.test is not None:
        for i in (args.test or [0, 2, 10, 40, 60]):
            print(f"\n{'='*60}\n第 {i} 页 (PDF 0基)  模型={MODEL}\n{'='*60}")
            print(json.dumps(ocr_page(doc, i, client, prompt, pages_dir, cache_dir, use_cache),
                             ensure_ascii=False, indent=2))
        return

    n = doc.page_count
    print(f"《{title}》全书 {n} 页,并发 {args.workers},模型 {MODEL}")
    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(ocr_page, doc, i, client, prompt, pages_dir, cache_dir, use_cache): i
                for i in range(n)}
        done = 0
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                results[i] = {"page_type": "error", "pdf_page": i, "error": str(e)}
                print(f"  ✗ 第 {i} 页失败: {e}", file=sys.stderr)
            done += 1
            if done % 10 == 0 or done == n:
                print(f"  进度 {done}/{n}")

    book = assemble(list(results.values()), title, args.author)
    out_json = ROOT / "out" / f"{slug}.json"
    out_md = ROOT / "out" / f"{slug}.md"
    out_json.write_text(json.dumps(book, ensure_ascii=False, indent=2))
    out_md.write_text(to_markdown(book))
    ns = sum(len(c["sections"]) for c in book["chapters"])
    print(f"\n完成 → {out_json}\n      → {out_md}")
    print(f"章节 {len(book['chapters'])} 个,小节 {ns} 条,前置页 {len(book['front_matter'])} 页")


if __name__ == "__main__":
    main()
