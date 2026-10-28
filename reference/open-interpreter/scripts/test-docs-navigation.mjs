import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

async function groups(file) {
  return JSON.parse(await readFile(new URL(`../${file}`, import.meta.url), "utf8")).navigation.groups;
}

function pages(entries) {
  return entries.flatMap((entry) => typeof entry === "string" ? [entry] : pages(entry.pages));
}

test("task guides are distinct from automation interfaces in both navigations", async () => {
  for (const [file, guideTitle, automationTitle, prefix] of [
    ["docs.json", "Guides", "Automation", "docs/"],
    ["docs.zh.json", "指南", "自动化", "docs/zh/"],
  ]) {
    const navigation = await groups(file);
    const guides = navigation.find((group) => group.group === guideTitle);
    const automation = navigation.find((group) => group.group === automationTitle);
    assert.ok(guides && automation, `${file}: missing section`);
    assert.deepEqual(guides.pages, ["prompting", "workflows", "streaming-web-chat", "github-action", "remote"]
      .map((slug) => `${prefix}${slug}`));
    assert.deepEqual(automation.pages, ["exec", "sdk", "app-server", "acp", "mcp-server"]
      .map((slug) => `${prefix}${slug}`));
    const allPages = navigation.flatMap((group) => pages(group.pages));
    assert.equal(new Set(allPages).size, allPages.length, `${file}: duplicate route`);
  }
});
