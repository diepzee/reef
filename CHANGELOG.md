## [2.4.1](https://github.com/diepzee/reef/compare/v2.4.0...v2.4.1) (2026-11-24)

### Bug fixes

* **search:** widen Postgres's own excerpt into the passage, never shorter than today (RUG-64) ([#163](https://github.com/diepzee/reef/issues/163)) ([07ba47d](https://github.com/diepzee/reef/commit/07ba47d84b50214082803d3c62cf7dabddae6ae5))

## [2.4.0](https://github.com/diepzee/reef/compare/v2.3.0...v2.4.0) (2026-11-24)

### Features

* **conversations:** test runs use the command-line tool's sign-in for Codex too (RUG-66) ([#160](https://github.com/diepzee/reef/issues/160)) ([afba60f](https://github.com/diepzee/reef/commit/afba60f2c3d56dc780f53528ad9afb6879dec4b3))

### Bug fixes

* **conversations:** clearer scenarios, one-of tool rules, and runner details (RUG-69) ([#162](https://github.com/diepzee/reef/issues/162)) ([2829046](https://github.com/diepzee/reef/commit/2829046f124cf0c3ef3c1e203dee2e86f4391ac2))

## [2.3.0](https://github.com/diepzee/reef/compare/v2.2.0...v2.3.0) (2026-11-23)

### Features

* **conversations:** run test conversations in their own Codex home (RUG-66) ([#157](https://github.com/diepzee/reef/issues/157)) ([1f6e87d](https://github.com/diepzee/reef/commit/1f6e87d5c45c75023280916eee7b24b6b2faea8f))

### Bug fixes

* **test-accounts:** verify sign-ins with the running server, and declare httpx for the CLI ([#159](https://github.com/diepzee/reef/issues/159)) ([2b9f723](https://github.com/diepzee/reef/commit/2b9f72309f85688f19fa19a5317562bb29e9df30))

## [2.2.0](https://github.com/diepzee/reef/compare/v2.1.0...v2.2.0) (2026-11-23)

### Features

* **calls:** say on the privacy page that the call log is on (RUG-45) ([#154](https://github.com/diepzee/reef/issues/154)) ([06e7ad1](https://github.com/diepzee/reef/commit/06e7ad1fda84726c0c5b14d9231739bed787841b))
* **test-accounts:** test conversations on a made-up account (RUG-63) ([#155](https://github.com/diepzee/reef/issues/155)) ([b541140](https://github.com/diepzee/reef/commit/b5411409bcacd76111395ffb6e1ae7bb579d4091))

## [2.1.0](https://github.com/diepzee/reef/compare/v2.0.0...v2.1.0) (2026-11-21)

### Features

* **calls:** ready the call log for switch-on (RUG-45) ([#151](https://github.com/diepzee/reef/issues/151)) ([2228bad](https://github.com/diepzee/reef/commit/2228bad71b7d0c53b93d2113c4f77fcde723cd11))
* **files:** read the text inside stored files, switched off for now (RUG-37) ([#153](https://github.com/diepzee/reef/issues/153)) ([340c878](https://github.com/diepzee/reef/commit/340c878494dd97beff072c19c8903ea4c3bf109c))

## [2.0.0](https://github.com/diepzee/reef/compare/v1.0.1...v2.0.0) (2026-11-20)

### ⚠ BREAKING CHANGES

* **mcp:** the MCP tools are replaced by 16 workflow tools; old names answer with their replacement. The Claude Code plugin and the command-line tool move to the new tools.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

### Features

* **eval:** a local test account of made-up facts (RUG-34) ([#134](https://github.com/diepzee/reef/issues/134)) ([d3b2bc0](https://github.com/diepzee/reef/commit/d3b2bc0ce9a6bc4fe2f7b0648075a914e17ed481))
* **eval:** load your own export as a private test set (RUG-34) ([#132](https://github.com/diepzee/reef/issues/132)) ([1e60ee6](https://github.com/diepzee/reef/commit/1e60ee658f5c5404485cc4eaae3cf0fefd0f23df))
* **eval:** measure retrieval on a public benchmark (RUG-34) ([#130](https://github.com/diepzee/reef/issues/130)) ([4e134fc](https://github.com/diepzee/reef/commit/4e134fc4a5aec225d65283b512ab700ebe6ef405))
* **links:** store wiki links, and show what links to a page (RUG-36) ([#128](https://github.com/diepzee/reef/issues/128)) ([9d043b8](https://github.com/diepzee/reef/commit/9d043b882d00a2ddc0dad22e98eba2ad8add4434))
* **mcp:** sixteen workflow tools, and spaces and engrams everywhere (RUG-40) ([#148](https://github.com/diepzee/reef/issues/148)) ([2371f10](https://github.com/diepzee/reef/commit/2371f10db7a7470bcc705f375b15069ad28d3be4))
* **memory:** foundations for the memory engine (RUG-33) ([#125](https://github.com/diepzee/reef/issues/125)) ([2455aa8](https://github.com/diepzee/reef/commit/2455aa8341ee6c0b076158249b325b924497eac8))
* **search:** cut a page into the pieces that get embedded (RUG-35) ([#135](https://github.com/diepzee/reef/issues/135)) ([9e82af1](https://github.com/diepzee/reef/commit/9e82af165e4f3369c866d1806720496151fffcf5))
* **search:** find a page that lacks one of the query's words ([#131](https://github.com/diepzee/reef/issues/131)) ([c0a7c10](https://github.com/diepzee/reef/commit/c0a7c108860e6309cfe690cf6c185e634f8db50a))
* **search:** find pages by meaning as well as by words (RUG-35) ([#138](https://github.com/diepzee/reef/issues/138)) ([3b22e66](https://github.com/diepzee/reef/commit/3b22e66b15251c10c47733ea2e9dfa1db5a8add3))
* **search:** log searches privately, for ninety days (RUG-34) ([#133](https://github.com/diepzee/reef/issues/133)) ([f70e035](https://github.com/diepzee/reef/commit/f70e0356e8caed1e7ff9006a8f940e7b5d31e43f))
* **search:** read the first results again with a reranker (RUG-43) ([#140](https://github.com/diepzee/reef/issues/140)) ([81b8134](https://github.com/diepzee/reef/commit/81b8134cfa3b9771949d734846201834aedfc1da))
* **search:** rerank only a memory of at least 1,000 pages (RUG-44) ([#145](https://github.com/diepzee/reef/issues/145)) ([afb6643](https://github.com/diepzee/reef/commit/afb664310396cd3204ac28c2cbdee07cace4b429))
* **search:** store the vectors of page pieces, and keep them current (RUG-35) ([#136](https://github.com/diepzee/reef/issues/136)) ([e290cc6](https://github.com/diepzee/reef/commit/e290cc6bcce60859cb00257af6ce8026f1b6994e))
* **usage:** record how each person uses their pages (RUG-23) ([#127](https://github.com/diepzee/reef/issues/127)) ([2b5205a](https://github.com/diepzee/reef/commit/2b5205ad89cfd00cd8d57af39e75467bc9c8935e))
* **worker:** report each job to Logfire, without its content ([#129](https://github.com/diepzee/reef/issues/129)) ([b2eff86](https://github.com/diepzee/reef/commit/b2eff86d39d7ebe69d2d75c45f64746440e832d9))

### Bug fixes

* **db:** create the avatar helpers production never got ([#149](https://github.com/diepzee/reef/issues/149)) ([07783fa](https://github.com/diepzee/reef/commit/07783fa4974650e3014eb4a285de9172b69f8e4f)), closes [#99](https://github.com/diepzee/reef/issues/99)
* **pages:** a page shows when it last changed, not when it was made ([#150](https://github.com/diepzee/reef/issues/150)) ([23b0d3c](https://github.com/diepzee/reef/commit/23b0d3c2cfaee213d3a3149abf25751d30018288))
* **search:** a file gets credit for meaning only in short queries (RUG-44) ([#144](https://github.com/diepzee/reef/issues/144)) ([9cb6006](https://github.com/diepzee/reef/commit/9cb600692921a07b0de2d75eaa11f2fbc5bde9a2))
* **site:** credit rugvin in public footers ([#124](https://github.com/diepzee/reef/issues/124)) ([4cba886](https://github.com/diepzee/reef/commit/4cba8867e406fd8352c8783a5505e8d1a422b656))
* **site:** say on the privacy page that the search log is on ([#139](https://github.com/diepzee/reef/issues/139)) ([9c8352d](https://github.com/diepzee/reef/commit/9c8352d2a34d5b2e0a13728ba77ad4ce4b65a9cc))

### Refactoring

* **config:** group every setting by what it is for ([#141](https://github.com/diepzee/reef/issues/141)) ([407c0cb](https://github.com/diepzee/reef/commit/407c0cb0199e5fe6ff0d357398fca0de5db56544))

### Documentation

* **backups:** separate upload checks from restore proof ([#122](https://github.com/diepzee/reef/issues/122)) ([b210679](https://github.com/diepzee/reef/commit/b210679a00b3c544cbb743d0a80667abf1bdaaeb))
* **runbook:** the reranker's sleep, measured, and the redeploy trap (RUG-44) ([#147](https://github.com/diepzee/reef/issues/147)) ([ae15bd2](https://github.com/diepzee/reef/commit/ae15bd2e39cbf15124f7ad583e30d420247367f1))
* **site:** say on the privacy page how search reads your pages (RUG-44) ([#142](https://github.com/diepzee/reef/issues/142)) ([efdde62](https://github.com/diepzee/reef/commit/efdde6233ad4d717f10e1fac1644cd7ef2204c26))

### Chores

* **deploy:** the model services, as Dockerfiles and a runbook section (RUG-44) ([#143](https://github.com/diepzee/reef/issues/143)) ([02dadfb](https://github.com/diepzee/reef/commit/02dadfb0eef4e419d9bb39ce018d31c9026ca178))
* **deploy:** the reranker as it runs on Railway (RUG-44) ([#146](https://github.com/diepzee/reef/issues/146)) ([1014a01](https://github.com/diepzee/reef/commit/1014a017d4588c4cac655c8347c10b06ae2644b8))
* **eval:** offline reranker experiment, and its results (RUG-35) ([#137](https://github.com/diepzee/reef/issues/137)) ([290a80f](https://github.com/diepzee/reef/commit/290a80f31ecf9fd7bc06a5f76b3a7a236c1b9145))

## [1.0.1](https://github.com/diepzee/reef/compare/v1.0.0...v1.0.1) (2026-08-24)

### Documentation

* **releasing:** a title without a prefix strands the release, silently ([#120](https://github.com/diepzee/reef/issues/120)) ([3eca12c](https://github.com/diepzee/reef/commit/3eca12ce0be7f8cfd7e0a66ef40f066c8c3b35b9))

## [1.0.0](https://github.com/diepzee/reef/compare/v0.6.0...v1.0.0) (2026-08-20)

### ⚠ BREAKING CHANGES

* list_spaces, create_space, delete_space and leave_space are
now list_coves, create_cove, delete_cove and leave_cove, and every tool that
took a `space` argument takes `cove`. Assistants re-read the tool list and
follow on their own; saved prompts and CLI scripts naming the old spellings
need updating.


Claude-Session: https://claude.ai/code/session_011TM36bcfT9x9CitXZPk34p

Co-authored-by: Claude Opus 5 <noreply@anthropic.com>

### Features

* **site:** add a searchable documentation page at /docs ([#107](https://github.com/diepzee/reef/issues/107)) ([1b173d8](https://github.com/diepzee/reef/commit/1b173d88d58356e16664b53c0db392a89151a3f9))
* **site:** say free is not a forever promise, before anyone signs in ([#105](https://github.com/diepzee/reef/issues/105)) ([3561f42](https://github.com/diepzee/reef/commit/3561f425c1df9c01ad06a151f869af2a67db4c18))

### Refactoring

* a shared memory is a cove everywhere, not a space ([#111](https://github.com/diepzee/reef/issues/111)) ([efcbedf](https://github.com/diepzee/reef/commit/efcbedf572f6743b41b89b59e0412ef424ee9b52))

### Chores

* create reef_authz on new clusters, and rename it on old ones ([#110](https://github.com/diepzee/reef/issues/110)) ([da7ac39](https://github.com/diepzee/reef/commit/da7ac399ed4503132eddd55eedb21afca74482c7))

## [0.6.0](https://github.com/diepzee/reef/compare/v0.5.0...v0.6.0) (2026-08-19)

### Features

* ship reef as a Claude Code plugin, licensed and annotated ([#85](https://github.com/diepzee/reef/issues/85)) ([d1892f6](https://github.com/diepzee/reef/commit/d1892f690c1fe80320b2404167319dd9c120bdd5))

## [0.5.0](https://github.com/diepzee/reef/compare/v0.4.0...v0.5.0) (2026-08-18)

### Features

* **site:** let the door write its own sentence ([#83](https://github.com/diepzee/reef/issues/83)) ([82d1504](https://github.com/diepzee/reef/commit/82d15047a8985a926f56b42fc783959312195e65))
* **site:** make the marketing site findable ([#82](https://github.com/diepzee/reef/issues/82)) ([f95a47d](https://github.com/diepzee/reef/commit/f95a47d93e15c63b02ac37201677e7117cfe9fb9))
* **site:** refine landing page rhythm ([#80](https://github.com/diepzee/reef/issues/80)) ([c9684d2](https://github.com/diepzee/reef/commit/c9684d2ecc5c98e4a6c4b61c60adef4c110589f1))

## [0.4.0](https://github.com/diepzee/reef/compare/v0.3.2...v0.4.0) (2026-08-17)

### Features

* **site:** add depth to the hero reef ([#78](https://github.com/diepzee/reef/issues/78)) ([504ce25](https://github.com/diepzee/reef/commit/504ce25fa3ae4645c7ead3b4fb7b393ba6477026))

## [0.3.2](https://github.com/diepzee/reef/compare/v0.3.1...v0.3.2) (2026-08-17)

### Bug fixes

* **site:** remove the background rings ([f111312](https://github.com/diepzee/reef/commit/f111312b18975e692a6d23ac3cb346d87278ed31))
* **site:** remove the hero badge ([#74](https://github.com/diepzee/reef/issues/74)) ([13b22a6](https://github.com/diepzee/reef/commit/13b22a66dffe8bbbeaea567f9e2a2284057610b6))

### CI

* harden the release pipeline the first real release exposed ([#77](https://github.com/diepzee/reef/issues/77)) ([daa5374](https://github.com/diepzee/reef/commit/daa537410b5405c3ec7591e8d53f0052cefedf74))

## [0.3.1](https://github.com/diepzee/reef/compare/v0.3.0...v0.3.1) (2026-08-17)

## [0.3.0](https://github.com/diepzee/reef/compare/v0.2.0...v0.3.0) (2026-08-17)

### Features

* **site:** add underwater depth to the landing page ([a733273](https://github.com/diepzee/reef/commit/a7332737e47884b126abef6910bd81eb78e5ce16))

## [0.2.0](https://github.com/diepzee/reef/compare/v0.1.0...v0.2.0) (2026-08-17)

### Features

* **release-notes:** tell people what shipped, and give the CLIs real version numbers ([#65](https://github.com/diepzee/reef/issues/65)) ([e17c385](https://github.com/diepzee/reef/commit/e17c38511eef3116a310c9d6f2e572eb930e707b))
