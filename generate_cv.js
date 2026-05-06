/**
 * generate_cv.js — Converts tailored CV text into a formatted Word document.
 * Called by output_generator.py via subprocess.
 * Usage: node generate_cv.js <input_txt_path> <output_docx_path>
 */

const {
  Document, Packer, Paragraph, TextRun, AlignmentType,
  HeadingLevel, BorderStyle, LevelFormat, WidthType, PageNumber,
  Header, Footer, TabStopType, TabStopPosition,
} = require("docx");
const fs = require("fs");

// ── Parse raw CV text into sections ─────────────────────────────────────────
function parseCVText(text) {
  const lines = text.split("\n").map(l => l.trim()).filter(l => l.length > 0);
  const sections = [];
  let current = null;

  for (const line of lines) {
    // Detect section headers (ALL CAPS or markdown ##)
    const isHeader = /^#{1,3}\s/.test(line) || /^[A-Z][A-Z\s\/&]{4,}$/.test(line);
    const cleanLine = line.replace(/^#{1,3}\s*/, "");

    if (isHeader) {
      if (current) sections.push(current);
      current = { header: cleanLine.toUpperCase(), items: [] };
    } else if (current) {
      current.items.push(line);
    } else {
      // Before any header — name/contact block
      sections.push({ header: "__CONTACT__", items: [line] });
    }
  }
  if (current) sections.push(current);
  return sections;
}

// ── Colour palette ───────────────────────────────────────────────────────────
const ACCENT = "1B4F72";      // dark navy
const SUBTLE = "5D6D7E";      // grey for contact line
const RULE_COLOR = "2E86C1";  // blue rule under name

// ── Build docx paragraphs from sections ─────────────────────────────────────
function buildChildren(sections) {
  const children = [];

  // Bullet numbering config is set at doc level; reference it here
  const bulletRef = "cv-bullets";

  for (const section of sections) {
    // ── Contact / name block ──────────────────────────────────────────────
    if (section.header === "__CONTACT__") {
      const [name, ...rest] = section.items;

      // Name — large, bold, navy
      children.push(
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 0, after: 60 },
          children: [
            new TextRun({
              text: name || "",
              bold: true,
              size: 48,       // 24pt
              color: ACCENT,
              font: "Arial",
            }),
          ],
        })
      );

      // Contact line
      if (rest.length > 0) {
        children.push(
          new Paragraph({
            alignment: AlignmentType.CENTER,
            spacing: { before: 0, after: 120 },
            children: [
              new TextRun({
                text: rest.join("  |  "),
                size: 18,     // 9pt
                color: SUBTLE,
                font: "Arial",
              }),
            ],
          })
        );
      }

      // Blue rule under header block
      children.push(
        new Paragraph({
          spacing: { before: 0, after: 200 },
          border: {
            bottom: { style: BorderStyle.SINGLE, size: 8, color: RULE_COLOR, space: 4 },
          },
          children: [],
        })
      );
      continue;
    }

    // ── Section header ────────────────────────────────────────────────────
    children.push(
      new Paragraph({
        heading: HeadingLevel.HEADING_2,
        spacing: { before: 200, after: 80 },
        border: {
          bottom: { style: BorderStyle.SINGLE, size: 4, color: "D5D8DC", space: 2 },
        },
        children: [
          new TextRun({
            text: section.header,
            bold: true,
            size: 22,         // 11pt
            color: ACCENT,
            font: "Arial",
            allCaps: true,
          }),
        ],
      })
    );

    // ── Section body ──────────────────────────────────────────────────────
    for (const item of section.items) {
      // Detect sub-headers (company | role | date lines)
      if (/\|/.test(item) && !/^[-•]/.test(item)) {
        const parts = item.split("|").map(p => p.trim());
        children.push(
          new Paragraph({
            spacing: { before: 120, after: 40 },
            tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
            children: [
              new TextRun({ text: parts[0], bold: true, size: 20, font: "Arial" }),
              parts[1]
                ? new TextRun({ text: `  •  ${parts[1]}`, size: 20, font: "Arial", italics: true })
                : new TextRun(""),
              parts[2]
                ? new TextRun({ text: `\t${parts[2]}`, size: 18, color: SUBTLE, font: "Arial" })
                : new TextRun(""),
            ],
          })
        );
      }
      // Bullet points
      else if (/^[-•]/.test(item)) {
        const text = item.replace(/^[-•]\s*/, "");
        children.push(
          new Paragraph({
            numbering: { reference: bulletRef, level: 0 },
            spacing: { before: 0, after: 40 },
            children: [new TextRun({ text, size: 19, font: "Arial" })],
          })
        );
      }
      // Plain text / skills line
      else {
        children.push(
          new Paragraph({
            spacing: { before: 40, after: 40 },
            children: [new TextRun({ text: item, size: 19, font: "Arial" })],
          })
        );
      }
    }
  }

  return children;
}

// ── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  const [, , inputPath, outputPath] = process.argv;
  if (!inputPath || !outputPath) {
    console.error("Usage: node generate_cv.js <input.txt> <output.docx>");
    process.exit(1);
  }

  const rawText = fs.readFileSync(inputPath, "utf-8");
  const sections = parseCVText(rawText);
  const children = buildChildren(sections);

  const doc = new Document({
    numbering: {
      config: [
        {
          reference: "cv-bullets",
          levels: [
            {
              level: 0,
              format: LevelFormat.BULLET,
              text: "•",
              alignment: AlignmentType.LEFT,
              style: {
                paragraph: { indent: { left: 360, hanging: 180 } },
              },
            },
          ],
        },
      ],
    },
    styles: {
      default: {
        document: { run: { font: "Arial", size: 19 } },
      },
      paragraphStyles: [
        {
          id: "Heading2",
          name: "Heading 2",
          basedOn: "Normal",
          next: "Normal",
          run: { size: 22, bold: true, font: "Arial", color: ACCENT },
          paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 1 },
        },
      ],
    },
    sections: [
      {
        properties: {
          page: {
            size: { width: 12240, height: 15840 },
            margin: { top: 900, right: 1080, bottom: 900, left: 1080 },
          },
        },
        footers: {
          default: new Footer({
            children: [
              new Paragraph({
                alignment: AlignmentType.RIGHT,
                children: [
                  new TextRun({
                    children: ["Page ", PageNumber.CURRENT, " of ", PageNumber.TOTAL_PAGES],
                    size: 16,
                    color: SUBTLE,
                    font: "Arial",
                  }),
                ],
              }),
            ],
          }),
        },
        children,
      },
    ],
  });

  const buffer = await Packer.toBuffer(doc);
  fs.writeFileSync(outputPath, buffer);
  console.log(`CV saved to: ${outputPath}`);
}

main().catch(err => {
  console.error("Error generating CV:", err);
  process.exit(1);
});
