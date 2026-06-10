#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "report" / "03_individual_report_draft.md"
TARGET = ROOT / "report" / "03_individual_report_draft.tex"


def escape_text(text: str) -> str:
    repl = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    out = "".join(repl.get(ch, ch) for ch in text)
    out = out.replace("R²", r"$R^2$")
    return out


def inline_markup(text: str) -> str:
    code_spans: list[str] = []

    def hold_code(match: re.Match[str]) -> str:
        code_spans.append(r"\texttt{" + escape_text(match.group(1)) + "}")
        return f"@@CODE{len(code_spans) - 1}@@"

    text = re.sub(r"`([^`]+)`", hold_code, text)
    text = escape_text(text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\\textbf{\1}", text)
    text = re.sub(r"\*([^*]+)\*", r"\\emph{\1}", text)
    text = text.replace("--", r"--")
    for idx, code in enumerate(code_spans):
        text = text.replace(escape_text(f"@@CODE{idx}@@"), code)
    return text


def split_table_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def is_table_sep(line: str) -> bool:
    cells = split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c.strip()) for c in cells)


def table_to_latex(lines: list[str]) -> list[str]:
    header = split_table_row(lines[0])
    rows = [split_table_row(line) for line in lines[2:]]
    n = len(header)
    width = max(0.10, min(0.28, 0.94 / max(n, 1)))
    spec = " ".join([f">{{\\raggedright\\arraybackslash}}p{{{width:.3f}\\linewidth}}" for _ in header])
    size = r"\scriptsize" if n >= 6 else r"\small"
    out = [r"\begin{center}", size, r"\begin{longtable}{" + spec + "}"]
    out.append(r"\toprule")
    out.append(" & ".join(inline_markup(cell) for cell in header) + r" \\")
    out.append(r"\midrule")
    for row in rows:
        row = row + [""] * (n - len(row))
        out.append(" & ".join(inline_markup(cell) for cell in row[:n]) + r" \\")
    out.append(r"\bottomrule")
    out.append(r"\end{longtable}")
    out.append(r"\end{center}")
    return out


def convert_markdown(md: str) -> str:
    lines = md.splitlines()
    body: list[str] = []
    title = "Individual Report Draft"
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            body.append("")
            i += 1
            continue
        if line.startswith("# "):
            title = line[2:].strip()
            i += 1
            continue
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading == "Abstract":
                body.append(r"\section*{Abstract}")
                body.append(r"\addcontentsline{toc}{section}{Abstract}")
            elif heading.startswith("References") or heading.startswith("Appendix"):
                body.append(r"\section*{" + inline_markup(heading) + "}")
                body.append(r"\addcontentsline{toc}{section}{" + inline_markup(heading) + "}")
            else:
                heading = re.sub(r"^\d+\.\s*", "", heading)
                body.append(r"\section{" + inline_markup(heading) + "}")
            i += 1
            continue
        if line.startswith("### "):
            body.append(r"\subsection{" + inline_markup(line[4:].strip()) + "}")
            i += 1
            continue
        if line.startswith("!["):
            match = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", line.strip())
            if match:
                alt, path = match.groups()
                body.append(r"\begin{figure}[H]")
                body.append(r"\centering")
                body.append(r"\includegraphics[width=0.90\linewidth]{" + escape_text(path) + "}")
                body.append(r"\caption{" + inline_markup(alt or "Figure") + "}")
                body.append(r"\end{figure}")
            i += 1
            continue
        if line.lstrip().startswith("- "):
            body.append(r"\begin{itemize}")
            while i < len(lines) and lines[i].lstrip().startswith("- "):
                item = lines[i].lstrip()[2:].strip()
                body.append(r"\item " + inline_markup(item))
                i += 1
            body.append(r"\end{itemize}")
            continue
        if line.startswith("|") and i + 1 < len(lines) and is_table_sep(lines[i + 1]):
            table_lines = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                table_lines.append(lines[i])
                i += 1
            body.extend(table_to_latex(table_lines))
            continue
        body.append(inline_markup(line))
        i += 1

    preamble = rf"""% Auto-generated from report/03_individual_report_draft.md.
% Source of record remains the Markdown draft; review before submission.
\documentclass[11pt,a4paper]{{article}}
\usepackage[utf8]{{inputenc}}
\usepackage[T1]{{fontenc}}
\usepackage{{lmodern}}
\usepackage[margin=24mm]{{geometry}}
\usepackage{{graphicx}}
\usepackage{{booktabs}}
\usepackage{{longtable}}
\usepackage{{array}}
\usepackage{{float}}
\usepackage{{hyperref}}
\usepackage{{xcolor}}
\usepackage{{caption}}
\hypersetup{{
  colorlinks=true,
  linkcolor=blue!60!black,
  citecolor=green!50!black,
  urlcolor=blue!70!black
}}
\setlength{{\parskip}}{{0.55em}}
\setlength{{\parindent}}{{0pt}}
\title{{{inline_markup(title)}}}
\author{{Zekai Xiao}}
\date{{Erlangen dataset, 28 May 2026}}
\begin{{document}}
\maketitle
\tableofcontents
\newpage
"""
    return preamble + "\n".join(body) + "\n\\end{document}\n"


def main() -> int:
    TARGET.write_text(convert_markdown(SOURCE.read_text(encoding="utf-8")), encoding="utf-8")
    print(TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
