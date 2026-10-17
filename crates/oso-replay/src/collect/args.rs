//! `oso-rec collect` command line

use super::session::{Handed, Level, valid_height_bin, valid_player_id};
use std::path::PathBuf;

pub const COLLECT_USAGE: &str = "usage: oso-rec collect --player P07 [--protocol drills-v1|FILE] [--out DIR] [--handed R|L] [--level beginner|club|advanced] [--height-cm-bin 170-175] [--no-view] [--force]";

#[derive(Debug, PartialEq)]
pub struct CollectArgs {
    pub player: String,
    pub protocol: String,
    /// None: `~/Datasets/oso-strokes/raw`
    pub out: Option<PathBuf>,
    pub handed: Option<Handed>,
    pub level: Option<Level>,
    pub height_bin: Option<String>,
    pub view: bool,
    pub force: bool,
}

impl CollectArgs {
    /// `args` are the words after `collect`
    pub fn parse(args: &[String]) -> anyhow::Result<CollectArgs> {
        let mut a = CollectArgs {
            player: String::new(),
            protocol: "drills-v1".into(),
            out: None,
            handed: None,
            level: None,
            height_bin: None,
            view: true,
            force: false,
        };
        let mut it = args.iter();
        while let Some(f) = it.next() {
            let mut val = || it.next().ok_or_else(|| anyhow::anyhow!("{f} needs a value\n{COLLECT_USAGE}"));
            match f.as_str() {
                "--player" => a.player = val()?.clone(),
                "--protocol" => a.protocol = val()?.clone(),
                "--out" => a.out = Some(val()?.into()),
                "--handed" => a.handed = Some(val()?.parse()?),
                "--level" => a.level = Some(val()?.parse()?),
                "--height-cm-bin" => a.height_bin = Some(val()?.clone()),
                "--no-view" => a.view = false,
                "--force" => a.force = true,
                other => anyhow::bail!("unknown argument {other}\n{COLLECT_USAGE}"),
            }
        }
        anyhow::ensure!(valid_player_id(&a.player), "--player must be an anonymous ID like P07 (P + 2-3 digits), never a name\n{COLLECT_USAGE}");
        if let Some(h) = &a.height_bin {
            anyhow::ensure!(valid_height_bin(h), "--height-cm-bin must be a 5 cm bin like 170-175");
        }
        Ok(a)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(s: &str) -> anyhow::Result<CollectArgs> {
        CollectArgs::parse(&s.split_whitespace().map(String::from).collect::<Vec<_>>())
    }

    #[test]
    fn parses_flags_and_defaults() {
        let a = parse("--player P07").unwrap();
        assert_eq!((a.protocol.as_str(), a.view, a.force, a.out.is_some()), ("drills-v1", true, false, false));
        let a = parse("--player P123 --protocol smoke --out /tmp/x --handed L --level club --height-cm-bin 170-175 --no-view --force").unwrap();
        assert_eq!(
            a,
            CollectArgs {
                player: "P123".into(),
                protocol: "smoke".into(),
                out: Some("/tmp/x".into()),
                handed: Some(Handed::L),
                level: Some(Level::Club),
                height_bin: Some("170-175".into()),
                view: false,
                force: true,
            }
        );
    }

    #[test]
    fn rejects_names_bad_values_and_unknown_flags() {
        assert!(parse("").is_err());
        assert!(parse("--player Alice").is_err());
        assert!(parse("--player P7").is_err());
        assert!(parse("--player P07 --handed X").is_err());
        assert!(parse("--player P07 --level pro").is_err());
        assert!(parse("--player P07 --height-cm-bin 170-180").is_err());
        assert!(parse("--player P07 --bogus").is_err());
        assert!(parse("--player").is_err());
    }
}
