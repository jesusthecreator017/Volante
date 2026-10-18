// Route table. The pod landing is the site; the Arena lives under /arena (old Arena paths redirect: see router.ts).

import { notFound, getStarted, privacy } from "./pages/content";
import { howItWorks } from "./pages/how";
import { arenaHome } from "./pages/home";
import { ladder, twinsLadder } from "./pages/ladder";
import { landing } from "./pages/landing";
import { match } from "./pages/match";
import { model } from "./pages/model";
import { myModels } from "./pages/myModels";
import { settings } from "./pages/settings";
import { tournament, tournaments } from "./pages/tournament";
import { route, setNotFound } from "./router";

route("/", "Racquet pod", landing);
route("/settings", "Settings", settings);
route("/how-it-works", "How it works", howItWorks);
route("/privacy", "Privacy", privacy);
route("/arena", "Arena", arenaHome);
route("/arena/ladder", "Ladder · Arena", ladder);
route("/arena/twins", "Twins · Arena", twinsLadder);
route("/arena/models/:id", "Model · Arena", model);
route("/arena/matches/:id", "Match · Arena", match);
route("/arena/tournaments", "Tournaments · Arena", tournaments);
route("/arena/tournaments/:id", "Tournament · Arena", tournament);
route("/arena/my-models", "My models · Arena", myModels);
route("/arena/get-started", "Get started · Arena", getStarted);
setNotFound(notFound);
