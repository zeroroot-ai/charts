# Changelog

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
