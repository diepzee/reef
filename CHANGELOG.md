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
