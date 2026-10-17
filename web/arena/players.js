// The match header: model A's name in A's colour "vs" model B's, each with its build "P/S/R/A". No DOM except in `showPlayers`; text goes in
// via textContent only (a model name is user supplied).

/** "power/speed/reach/accuracy", e.g. "9/6/5/4". */
export function formatBuild(b) {
  return `${b.power}/${b.speed}/${b.reach}/${b.accuracy}`;
}

/** Fills the header for `info` ({a: {name, build}, b: {...}}) and shows it; hides it when `info` is null. */
export function showPlayers(doc, info) {
  const bar = doc.getElementById("players");
  if (!bar) return;
  bar.hidden = !info;
  if (!info) return;
  for (const who of ["a", "b"]) {
    doc.getElementById(`pl-${who}-name`).textContent = info[who].name;
    doc.getElementById(`pl-${who}-build`).textContent = formatBuild(info[who].build);
  }
}
