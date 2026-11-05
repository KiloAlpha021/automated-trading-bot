import { readdir, readFile } from "node:fs/promises";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../src/", import.meta.url));
const forbidden = ["fetch(", "XMLHttpRequest", "WebSocket", "process.env", "localStorage.setItem(\"credential"];
let failures = 0;

async function walk(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) await walk(path);
    else if ([".ts", ".tsx", ".css"].includes(extname(path))) {
      const text = await readFile(path, "utf8");
      text.split(/\r?\n/).forEach((line, index) => {
        if (/\s+$/.test(line)) {
          console.error(`${path}:${index + 1}: trailing whitespace`);
          failures += 1;
        }
      });
      for (const token of forbidden) {
        if (text.toLowerCase().includes(token.toLowerCase())) {
          console.error(`${path}: prohibited operational token: ${token}`);
          failures += 1;
        }
      }
    }
  }
}

await walk(root);
if (failures) process.exit(1);
console.log("Static UI boundary lint passed.");
