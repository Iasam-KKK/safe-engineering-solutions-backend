// Pull the project data out of the finished design (references/design/canvas) as JSON.
//
//   node seed/extract_design.mjs ../references/design/canvas > seed/design_raw.json
//
// Only constant data statements are evaluated (helpers, glyphs, SETS, RUN, PARTNERS, P);
// nothing that touches the page. Placeholder sheets come out as "ph:<glyph name>".
import fs from "node:fs";
import path from "node:path";

const dir = process.argv[2];
const read = (f) => fs.readFileSync(path.join(dir, f), "utf8");

// Find `const NAME = ...;` and return the whole statement (string/bracket aware).
// Searches from the data block (`const B = (id)`), past same-named locals in event handlers.
function stmt(src, name) {
  const start = src.indexOf(`const ${name} = `, src.indexOf("const B = (id)"));
  if (start < 0) throw new Error(`missing const ${name}`);
  let depth = 0, q = null;
  for (let i = start; i < src.length; i++) {
    const c = src[i];
    if (q) {
      if (c === "\\") { i++; continue; }
      if (c === q) q = null;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") q = c;
    else if ("([{".includes(c)) depth++;
    else if (")]}".includes(c)) depth--;
    else if (c === ";" && depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(`unterminated const ${name}`);
}

const GLYPHS = ["towerGlyph", "plateGlyph", "footGlyph", "arrayGlyph", "frameGlyph", "pierGlyph",
  "containerGlyph", "floatGlyph", "glassGlyph", "planGlyph", "sectionGlyph"];
const GLYPH_NAME = { towerGlyph: "tower", plateGlyph: "plate", footGlyph: "footing", arrayGlyph: "array",
  frameGlyph: "frame", pierGlyph: "pier", containerGlyph: "container", floatGlyph: "float", glassGlyph: "glass",
  planGlyph: "plan", sectionGlyph: "section" };

function run(src, helpers, out) {
  const code = [
    'const B = (id) => "blob:" + id;',
    ...helpers.map((h) => stmt(src, h)),
    ...GLYPHS.map((g) => stmt(src, g)),
    "const __g = {" + GLYPHS.map((g) => `${JSON.stringify(GLYPH_NAME[g])}: ${g}`).join(",") + "};",
    "const phSheet = (no, title, glyph) => 'ph:' + Object.keys(__g).find((k) => __g[k] === glyph);",
    ...out.map((o) => stmt(src, o)),
    "return {" + out.join(",") + "};",
  ].join("\n");
  return new Function(code)();
}

const home = read("DraftingTable.dc.html");
const projects = read("Projects.dc.html");
const result = {
  home: run(home, ["W", "G", "D", "sh", "pr"], ["SETS", "RUN", "PARTNERS"]),
  projects: run(projects, ["W", "G", "cmp", "phCmp", "view"], ["P"]),
};
process.stdout.write(JSON.stringify(result, null, 1));
