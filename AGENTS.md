# Agent Workflows — Agent Guide

This repo holds shared, project-agnostic workflows for AI agents. Changes here follow its own [principles](principles.md) and [feature delivery workflow](workflows/feature-delivery.md); the maintainer is `deepconcern`, who merges every PR.

## Rules for Changes Here

- **Keep it generic.** No project names, people's names, hostnames, emails, or secrets. Anything specific to one project belongs in that project's `AGENTS.md`. The skills read project values (maintainer, labels, check command, domain docs) from the project's `AGENTS.md` and must keep working without edits in any project.
- **Plain Markdown.** Any agent, with or without repo access, should be able to follow every file. Skills keep the `name` and `description` frontmatter Claude Code and Copilot load.
- **Links inside `skills/` stay inside `skills/`.** The installer copies skills to a project's `.claude/skills/` and the rest to `.agent-workflows/`, so a relative link from a skill to a doc outside `skills/` would break. Refer to those docs by their installed path instead.
- **Every change updates [`CHANGELOG.md`](CHANGELOG.md)** under an "Unreleased" heading, classified by the versioning rules in the [README](README.md#versioning). Releases are tagged on `main` after the maintainer merges, by the maintainer or by an agent they ask.
- **Test `install.sh` changes** by running it twice against a scratch project: the second run must change nothing.
