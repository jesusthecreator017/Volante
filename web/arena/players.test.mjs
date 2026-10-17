import test from "node:test";
import assert from "node:assert/strict";
import { formatBuild, showPlayers } from "./players.js";

// The smallest DOM the module touches
function fakeDoc(ids) {
  const els = Object.fromEntries(ids.map((id) => [id, { hidden: true, textContent: "" }]));
  return { els, getElementById: (id) => els[id] ?? null };
}
const IDS = ["players", "pl-a-name", "pl-a-build", "pl-b-name", "pl-b-build"];
const INFO = {
  a: { name: "Ada <b>", build: { power: 9, speed: 6, reach: 5, accuracy: 4 } },
  b: { name: "Bo", build: { power: 3, speed: 8, reach: 8, accuracy: 5 } },
};

test("build line is P/S/R/A", () => {
  assert.equal(formatBuild(INFO.a.build), "9/6/5/4");
  assert.equal(formatBuild(INFO.b.build), "3/8/8/5");
});

test("showPlayers writes names and builds as text and shows the bar", () => {
  const d = fakeDoc(IDS);
  showPlayers(d, INFO);
  assert.equal(d.els.players.hidden, false);
  assert.equal(d.els["pl-a-name"].textContent, "Ada <b>"); // textContent: markup stays text
  assert.equal(d.els["pl-a-build"].textContent, "9/6/5/4");
  assert.equal(d.els["pl-b-name"].textContent, "Bo");
  assert.equal(d.els["pl-b-build"].textContent, "3/8/8/5");
});

test("no match info hides the bar; a page without the bar is fine", () => {
  const d = fakeDoc(IDS);
  showPlayers(d, INFO);
  showPlayers(d, null);
  assert.equal(d.els.players.hidden, true);
  assert.doesNotThrow(() => showPlayers(fakeDoc([]), INFO));
});
