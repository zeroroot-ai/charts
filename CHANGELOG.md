# Changelog

## [0.130.0](https://github.com/zeroroot-ai/charts/compare/v0.129.0...v0.130.0) (2026-09-15)


### Features

* **tenant-neo4j:** the chart delivers the per-tier sizing it always claimed to own ([#99](https://github.com/zeroroot-ai/charts/issues/99)) ([b86bb35](https://github.com/zeroroot-ai/charts/commit/b86bb35703756a3c778dc0318eeb55d24a29af87))

## [0.129.0](https://github.com/zeroroot-ai/charts/compare/v0.128.0...v0.129.0) (2026-09-15)


### Features

* **profile:** developer and CI become rungs the chart ships ([#97](https://github.com/zeroroot-ai/charts/issues/97)) ([86d86e7](https://github.com/zeroroot-ai/charts/commit/86d86e7220222e34f959b34de7577cc50dbf7539))
* **publish:** prove every published chart pulls with no credential ([#98](https://github.com/zeroroot-ai/charts/issues/98)) ([a276718](https://github.com/zeroroot-ai/charts/commit/a2767187d0b632794646413d68820f0136e24797)), closes [#94](https://github.com/zeroroot-ai/charts/issues/94)


### Bug Fixes

* **ci:** pin the org tree guards to a commit SHA ([#90](https://github.com/zeroroot-ai/charts/issues/90)) ([634b67e](https://github.com/zeroroot-ai/charts/commit/634b67e7fbc43546a536b52e990289d105076118))

## [0.128.0](https://github.com/zeroroot-ai/charts/compare/v0.127.2...v0.128.0) (2026-09-15)


### Features

* **vanilla-up:** install the PUBLISHED charts, not only a checkout ([#85](https://github.com/zeroroot-ai/charts/issues/85)) ([075200c](https://github.com/zeroroot-ai/charts/commit/075200ccfefacd35662ced807c04757d6ec0b4ab)), closes [#79](https://github.com/zeroroot-ai/charts/issues/79)


### Bug Fixes

* **images:** pin the dashboard to a release tag, not a moving main ([#89](https://github.com/zeroroot-ai/charts/issues/89)) ([ee72ad1](https://github.com/zeroroot-ai/charts/commit/ee72ad14a778c9ca859662d66cc6d6207d7cc438))
* **netpol:** OpenBao needs apiserver egress, on every CNI that enforces policy ([#87](https://github.com/zeroroot-ai/charts/issues/87)) ([675de0c](https://github.com/zeroroot-ai/charts/commit/675de0c446d84025271779ab8841c715c328f382)), closes [#80](https://github.com/zeroroot-ai/charts/issues/80)

## [0.127.2](https://github.com/zeroroot-ai/charts/compare/v0.127.1...v0.127.2) (2026-09-14)


### Bug Fixes

* **crds:** helm install of gibson-crds blew the 1 MiB release-record cap ([#83](https://github.com/zeroroot-ai/charts/issues/83)) ([06adad1](https://github.com/zeroroot-ai/charts/commit/06adad188be982dcf04b9d4964740883f44980e6))

## [0.127.1](https://github.com/zeroroot-ai/charts/compare/v0.127.0...v0.127.1) (2026-09-14)


### Bug Fixes

* **branding:** the login page serves the acid-concrete brand, and a rebrand reaches a branded instance ([#81](https://github.com/zeroroot-ai/charts/issues/81)) ([4ab1291](https://github.com/zeroroot-ai/charts/commit/4ab12918a80b59bd8f8051374578eaba5394d55b))
* **netpol:** the CNPG operator's Jobs may reach the primary, so replicas join ([#77](https://github.com/zeroroot-ai/charts/issues/77)) ([6d427d3](https://github.com/zeroroot-ai/charts/commit/6d427d3c34c8a0c59a4f033466586d03f90c0f3d))

## [0.127.0](https://github.com/zeroroot-ai/charts/compare/v0.126.2...v0.127.0) (2026-09-14)


### Features

* **netpol:** a label seam for platform-postgres clients, so no overlay writes a policy on our pods ([#75](https://github.com/zeroroot-ai/charts/issues/75)) ([c56f464](https://github.com/zeroroot-ai/charts/commit/c56f4648085c0bb7384ada1ed9451b519f96c981))

## [0.126.2](https://github.com/zeroroot-ai/charts/compare/v0.126.1...v0.126.2) (2026-09-11)


### Bug Fixes

* **openbao:** keyring inputs follow the keyring without a pod restart ([#73](https://github.com/zeroroot-ai/charts/issues/73)) ([c4f2838](https://github.com/zeroroot-ai/charts/commit/c4f28383608ca5630cfa7df34b15ed7839e1befa))

## [0.126.1](https://github.com/zeroroot-ai/charts/compare/v0.126.0...v0.126.1) (2026-09-11)


### Bug Fixes

* **images:** re-pin gibson, ext-authz and the three operators to v0.136.0 ([#71](https://github.com/zeroroot-ai/charts/issues/71)) ([1b1fc4d](https://github.com/zeroroot-ai/charts/commit/1b1fc4d9fa83275e860ec25380533cb242d24891))

## [0.126.0](https://github.com/zeroroot-ai/charts/compare/v0.125.1...v0.126.0) (2026-09-11)


### ⚠ BREAKING CHANGES

* **seams:** secrets.llm.existingSecret is gone; an operator wires LLM providers per tenant, never on the platform.

### Features

* **seams:** no platform LLM credential ([#69](https://github.com/zeroroot-ai/charts/issues/69)) ([9190641](https://github.com/zeroroot-ai/charts/commit/91906415d7b28de05227a3e2e2e7855fbf9bf399))

## [0.125.1](https://github.com/zeroroot-ai/charts/compare/v0.125.0...v0.125.1) (2026-09-10)


### Bug Fixes

* **daemon:** always hand the LLM-keys Secret to the daemon ([#67](https://github.com/zeroroot-ai/charts/issues/67)) ([3efd616](https://github.com/zeroroot-ai/charts/commit/3efd616319998466748f22342dad737dd0e369cc))

## [0.125.0](https://github.com/zeroroot-ai/charts/compare/v0.124.2...v0.125.0) (2026-09-10)


### Features

* **seams:** one Route53 credential Secret for cert-manager and external-dns, one SMTP relay credential for the platform ([#65](https://github.com/zeroroot-ai/charts/issues/65)) ([0e605d8](https://github.com/zeroroot-ai/charts/commit/0e605d8570b6029916600c723ec57e3defa86183))

## [0.124.2](https://github.com/zeroroot-ai/charts/compare/v0.124.1...v0.124.2) (2026-09-10)


### Bug Fixes

* **netpol:** let OpenBao reach the kube-apiserver where egress to it is policed ([#62](https://github.com/zeroroot-ai/charts/issues/62)) ([249e0f8](https://github.com/zeroroot-ai/charts/commit/249e0f8a74ec417e4f75ab2a77728be4c4921e30))

## [0.124.1](https://github.com/zeroroot-ai/charts/compare/v0.124.0...v0.124.1) (2026-09-10)


### Bug Fixes

* **images:** re-pin gibson, ext-authz and the three operators to v0.135.1 ([#60](https://github.com/zeroroot-ai/charts/issues/60)) ([9d82545](https://github.com/zeroroot-ai/charts/commit/9d8254544799a587b42850d490f2b3245cef769d))
* **rbac:** let the first-admin Job create the founding TenantMember it documents creating ([#58](https://github.com/zeroroot-ai/charts/issues/58)) ([ada2d5c](https://github.com/zeroroot-ai/charts/commit/ada2d5c64f4a72bcaf6ba963e27e6ca46d5f283e))
* **seed:** mint passwords from letters and digits, never a leading dash ([#57](https://github.com/zeroroot-ai/charts/issues/57)) ([97a16f0](https://github.com/zeroroot-ai/charts/commit/97a16f0275f41f061aa05dc4eb54bb8b467cca64))

## [0.124.0](https://github.com/zeroroot-ai/charts/compare/v0.123.15...v0.124.0) (2026-09-09)


### Features

* **imagecreds:** add a provider-neutral ESO generator source for the pull token ([#54](https://github.com/zeroroot-ai/charts/issues/54)) ([f60f6ce](https://github.com/zeroroot-ai/charts/commit/f60f6ceb9bf99e6e492e2b88ac25d2f31b6f923e))

## [0.123.15](https://github.com/zeroroot-ai/charts/compare/v0.123.14...v0.123.15) (2026-09-09)


### Bug Fixes

* **zitadel:** bump to v4.17.3 ([#53](https://github.com/zeroroot-ai/charts/issues/53)) ([fefb1b7](https://github.com/zeroroot-ai/charts/commit/fefb1b75cfc7eefb8c944e7ba4d8bf067378071b))

## Changelog

This repository restarted from a fresh baseline on 2026-09-06. Release notes before that date are archived offline and do not resolve on GitHub. release-please adds each release below this line.
