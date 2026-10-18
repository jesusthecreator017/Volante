// Route table. The pod landing is the site; the Arena lives under /arena (old Arena paths redirect: see router.ts).

import { notFound, getStarted, privacy } from "./pages/content";
import { howItWorks } from "./pages/how";
import { arenaHome } from "./pages/home";
import { ladder } from "./pages/ladder";
import { landing } from "./pages/landing";
import { match } from "./pages/match";
import { model } from "./pages/model";
import { sessionPage } from "./pages/session";
import { settings } from "./pages/settings";
import { tournament, tournaments } from "./pages/tournament";
import { route, setNotFound } from "./router";

route("/", "Racquet pod", landing);
route("/settings", "Settings", settings);
route("/how-it-works", "How it works", howItWorks);
route("/report", "Session report", sessionPage);
route("/privacy", "Privacy", privacy);
route("/arena", "Arena", arenaHome);
route("/arena/ladder", "Ladder · Arena", ladder);
route("/arena/models/:id", "Model · Arena", model);
route("/arena/matches/:id", "Match · Arena", match);
route("/arena/tournaments", "Tournaments · Arena", tournaments);
route("/arena/tournaments/:id", "Tournament · Arena", tournament);
route("/arena/get-started", "Train & upload · Arena", getStarted);
route("/arena/my-models", "Train & upload · Arena", getStarted); // sign-in returns here
setNotFound(notFound);
