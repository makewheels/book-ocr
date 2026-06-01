# book-ocr

把**图片版 / 扫描版 PDF 图书**(没有文字层的那种)用通义千问视觉模型(qwen3-vl)逐页识别成**结构化 JSON + Markdown**。

适合按「章 → 小节」组织的中文图书:自动判别封面 / 序言 / 目录 / 章分隔页 / 正文小节,并把正文里【】小标题拆成字段。相比纯 OCR,VLM 能理解版面(如左右分栏不会串行读混)。

## 安装

```bash
uv sync
cp .env.example .env   # 填入 DASHSCOPE_API_KEY(阿里云百炼)
```

## 用法

```bash
# 先抽几页验证识别质量(0 基页号),不写文件
uv run pdf2json.py --pdf ~/path/某本书.pdf --test 0 2 10 40

# 跑全书 → out/<书名>.json 和 out/<书名>.md
uv run pdf2json.py --pdf ~/path/某本书.pdf --title 书名 --author 作者 \
    --desc "书籍类型" --hint "版面特征提示,提升准确度"
```

逐页结果会缓存到 `out/cache/<书名>/`,重跑只补失败页、不重复计费;改了标题/作者想重新组装,直接再跑(全走缓存,0 调用)。

可用 `MODEL` 环境变量切换模型(默认 `qwen3-vl-plus`,也可用 `qwen-vl-max` / `qwen-vl-plus`)。

## 输出结构

```jsonc
{
  "title": "书名", "author": "作者",
  "front_matter": [ { "page_type": "preface", "pdf_page": 2, "text": "..." } ],
  "chapters": [
    {
      "no": "第一章", "title": "章标题", "pdf_page": 16,
      "sections": [
        {
          "title": "小节标题", "printed_page": 12, "pdf_page": 18,
          "blocks": { "小标题A": "正文…", "小标题B": "正文…" }
        }
      ]
    }
  ]
}
```

## 注意(版权)

本工具仅用于处理**你有权处理的图书**(自有著作、公版书、授权材料等)。识别出的内容受原作版权约束,**请勿公开传播或再分发**受版权保护的图书全文。

## License

MIT(仅指本工具代码)
