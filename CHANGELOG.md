# Changelog

## [0.123.8](https://github.com/zeroroot-ai/charts/compare/v0.123.7...v0.123.8) (2026-09-07)


### Bug Fixes

* **tools:** one value for the alpine-k8s tool image across every job and init container ([#30](https://github.com/zeroroot-ai/charts/issues/30)) ([37dc01a](https://github.com/zeroroot-ai/charts/commit/37dc01a0b7cd8f1498bc9606e098e94423c3f23f))

## [0.123.7](https://github.com/zeroroot-ai/charts/compare/v0.123.6...v0.123.7) (2026-09-07)


### Bug Fixes

* **zitadel:** bump to v4.17.3 ([#26](https://github.com/zeroroot-ai/charts/issues/26)) ([b1e4712](https://github.com/zeroroot-ai/charts/commit/b1e471256390080909f4f480179bbeb96849dd49))

## [0.123.6](https://github.com/zeroroot-ai/charts/compare/v0.123.5...v0.123.6) (2026-09-07)


### Bug Fixes

* **deps:** bump openfga to v1.19.0, wait4x to v3.7.1 and the wait-for-system-key image to alpine-k8s 1.33.0 ([#25](https://github.com/zeroroot-ai/charts/issues/25)) ([9b56623](https://github.com/zeroroot-ai/charts/commit/9b5662370549b6c154d28c76ec4de0413fd3947e))
* **netpol:** the exit-test runner gets an isolation policy ([#27](https://github.com/zeroroot-ai/charts/issues/27)) ([ce8dfc9](https://github.com/zeroroot-ai/charts/commit/ce8dfc9baa195f07b95e8b41416af4a941ca85ef))

## [0.123.5](https://github.com/zeroroot-ai/charts/compare/v0.123.4...v0.123.5) (2026-09-07)


### Bug Fixes

* **zitadel:** lockstep guard for server and login versions, and the coordinated bump to v4.17.3 ([#20](https://github.com/zeroroot-ai/charts/issues/20)) ([be0827a](https://github.com/zeroroot-ai/charts/commit/be0827a06add5ca94938f6e69066dfddad862a54))

## [0.123.4](https://github.com/zeroroot-ai/charts/compare/v0.123.3...v0.123.4) (2026-09-07)


### Bug Fixes

* **verify:** re-prove the store and every ExternalSecret after the restart and keyring drills ([#22](https://github.com/zeroroot-ai/charts/issues/22)) ([4dabfea](https://github.com/zeroroot-ai/charts/commit/4dabfead6a00cd77e4b737ce8cde21439b9b0caf))

## [0.123.3](https://github.com/zeroroot-ai/charts/compare/v0.123.2...v0.123.3) (2026-09-07)


### Bug Fixes

* **verify:** the seam suite asserts the edge reset for www and probes signup through the dashboard ([#18](https://github.com/zeroroot-ai/charts/issues/18)) ([f6d1724](https://github.com/zeroroot-ai/charts/commit/f6d172477da86a4471a31adb638a17e31b6cd296))

## [0.123.2](https://github.com/zeroroot-ai/charts/compare/v0.123.1...v0.123.2) (2026-09-07)


### Bug Fixes

* **rbac:** let the daemon read Tenant objects across the cluster ([#15](https://github.com/zeroroot-ai/charts/issues/15)) ([3154a6b](https://github.com/zeroroot-ai/charts/commit/3154a6be50b4d92745117f17ffaed043b40a8338))

## [0.123.1](https://github.com/zeroroot-ai/charts/compare/v0.123.0...v0.123.1) (2026-09-07)


### Bug Fixes

* **ci:** commit gibson-catalog.ref with the regenerated air-gap manifest ([#12](https://github.com/zeroroot-ai/charts/issues/12)) ([59aaa9e](https://github.com/zeroroot-ai/charts/commit/59aaa9eaf2faa5c0f8834053094f9fada2691c03))
* **ci:** drop the vanilla-install exit test that cannot run here; it lives in hosted ([#10](https://github.com/zeroroot-ai/charts/issues/10)) ([d92a2a4](https://github.com/zeroroot-ai/charts/commit/d92a2a4e01c9cc378d60fdea85b2870d74d50c8a))

## [0.123.0](https://github.com/zeroroot-ai/charts/compare/v0.122.2...v0.123.0) (2026-09-06)


### Features

* **airgap:** list the dispatch-time images and check the manifest in CI ([#8](https://github.com/zeroroot-ai/charts/issues/8)) ([ea5f2fc](https://github.com/zeroroot-ai/charts/commit/ea5f2fcb214863233546e5cb274bdfadf185fbf0))

## [0.122.2](https://github.com/zeroroot-ai/charts/compare/v0.122.1...v0.122.2) (2026-09-06)


### Bug Fixes

* **images:** re-pin every first-party image to a build that exists ([#6](https://github.com/zeroroot-ai/charts/issues/6)) ([db6341b](https://github.com/zeroroot-ai/charts/commit/db6341b91a9ac23aa90dabf1bae35b696d8d8f39))

## [0.122.1](https://github.com/zeroroot-ai/charts/compare/v0.122.0...v0.122.1) (2026-09-06)


### Bug Fixes

* **airgap:** render the image manifest from the profiles that live in this repo ([#4](https://github.com/zeroroot-ai/charts/issues/4)) ([eec96c1](https://github.com/zeroroot-ai/charts/commit/eec96c13ed195d0365527f073c9c0fcd65375cb6))
* **airgap:** render the substrate profiles as overlays on values-vanilla ([#5](https://github.com/zeroroot-ai/charts/issues/5)) ([1f54bf1](https://github.com/zeroroot-ai/charts/commit/1f54bf1f6c32d77366bf4720f6bccdbfb8cdb9cb))
* **ci:** mint the release App token for charts, not the deleted deploy repo ([#2](https://github.com/zeroroot-ai/charts/issues/2)) ([488e246](https://github.com/zeroroot-ai/charts/commit/488e2465e5e1c48631e59fcb7f3389cd68626f1f))
