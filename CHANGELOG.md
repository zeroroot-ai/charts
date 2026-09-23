# Changelog

## [0.130.1](https://github.com/zeroroot-ai/charts/compare/v0.130.0...v0.130.1) (2026-09-23)


### Bug Fixes

* **rework:** the publish render passes the installer inputs ([#154](https://github.com/zeroroot-ai/charts/issues/154)) ([5eea0f2](https://github.com/zeroroot-ai/charts/commit/5eea0f2d15bcdca0cbd404024a6272826f7dba6d))
* **tenant:** the first-tenant seed names its plan ([#157](https://github.com/zeroroot-ai/charts/issues/157)) ([7b98311](https://github.com/zeroroot-ai/charts/commit/7b983110f6af916934b8724588e687638220b1ee))

## [0.130.0](https://github.com/zeroroot-ai/charts/compare/v0.129.0...v0.130.0) (2026-09-23)


### Features

* **ci:** kind and k3d are separate workflows, from one definition ([#105](https://github.com/zeroroot-ai/charts/issues/105)) ([1329196](https://github.com/zeroroot-ai/charts/commit/132919687d6343bf81eb91ab53d70446cf19eb09))
* **ci:** the published-install exit test moves here, and k3d comes back ([#101](https://github.com/zeroroot-ai/charts/issues/101)) ([a173e53](https://github.com/zeroroot-ai/charts/commit/a173e5350d2022b9a54f631d29fe63ac857bdb15))
* **ext-authz:** name the human sign-in client so machine tokens are machine credentials ([#118](https://github.com/zeroroot-ai/charts/issues/118)) ([2cdbadd](https://github.com/zeroroot-ai/charts/commit/2cdbaddf69acf83ec2b7d615df99c6e8bfd9d0de))
* **guards:** backup coverage, ext-authz transport and workload RBAC are contracts again ([#122](https://github.com/zeroroot-ai/charts/issues/122)) ([7369e5c](https://github.com/zeroroot-ai/charts/commit/7369e5c215fdeef8073d49ff3a6076909c380710)), closes [#17](https://github.com/zeroroot-ai/charts/issues/17)
* **guards:** image registry, orphan templates and swallowed probes are contracts again ([#119](https://github.com/zeroroot-ai/charts/issues/119)) ([7b87d5b](https://github.com/zeroroot-ai/charts/commit/7b87d5b12a907939a42ecfea64b61b5c81ca452e)), closes [#17](https://github.com/zeroroot-ai/charts/issues/17)
* **guards:** NetworkPolicy coverage and no literal platform hostname are contracts again ([#120](https://github.com/zeroroot-ai/charts/issues/120)) ([0289d7a](https://github.com/zeroroot-ai/charts/commit/0289d7a59dd994f22de409141a5101de72fd66a0)), closes [#17](https://github.com/zeroroot-ai/charts/issues/17)
* **guards:** no dangling Secret reference in the render is a contract again ([#121](https://github.com/zeroroot-ai/charts/issues/121)) ([bc9a290](https://github.com/zeroroot-ai/charts/commit/bc9a290a82f9bfb46df4aed8ee0bac64bf978025)), closes [#17](https://github.com/zeroroot-ai/charts/issues/17)
* **guards:** the render validates against the Kubernetes and CRD schemas, and it found a defect ([#124](https://github.com/zeroroot-ai/charts/issues/124)) ([59006d5](https://github.com/zeroroot-ai/charts/commit/59006d5680bf2943b67d21700518a425df658ec7)), closes [#17](https://github.com/zeroroot-ai/charts/issues/17)
* **guards:** webhook ordering and sweepability, one edge, and POSIX hook scripts are contracts again ([#123](https://github.com/zeroroot-ai/charts/issues/123)) ([60769d9](https://github.com/zeroroot-ai/charts/commit/60769d947ab671afac5c514789dea9927f4d11eb)), closes [#17](https://github.com/zeroroot-ai/charts/issues/17)
* **tenant-neo4j:** the chart delivers the per-tier sizing it always claimed to own ([#99](https://github.com/zeroroot-ai/charts/issues/99)) ([b86bb35](https://github.com/zeroroot-ai/charts/commit/b86bb35703756a3c778dc0318eeb55d24a29af87))


### Bug Fixes

* **chart-deps:** the retry runs, and a 504 on a sub-chart download no longer fails the job ([#147](https://github.com/zeroroot-ai/charts/issues/147)) ([6059700](https://github.com/zeroroot-ai/charts/commit/6059700497b1e891e26b21d83e2088f1d1230569))
* **ci:** pin every zeroroot-ai/.github reference to v0.5.1 ([#109](https://github.com/zeroroot-ai/charts/issues/109)) ([038d1cb](https://github.com/zeroroot-ai/charts/commit/038d1cbc5d1b36f71530bbefb47484ee49bcf616))
* **ci:** the k3d diagnostics survive the pod being reaped ([#104](https://github.com/zeroroot-ai/charts/issues/104)) ([5ab762c](https://github.com/zeroroot-ai/charts/commit/5ab762c45ffaa87acb0c528e927171ec1dfc83c7))
* **ci:** the published-install job names why the hosted token failed ([#102](https://github.com/zeroroot-ai/charts/issues/102)) ([e5cc6a8](https://github.com/zeroroot-ai/charts/commit/e5cc6a8fbf1b7e069280fa2f7e23afb8bf4c077e))
* **deps:** pin the gibson images to v0.137.0 ([#128](https://github.com/zeroroot-ai/charts/issues/128)) ([405abda](https://github.com/zeroroot-ai/charts/commit/405abdaa3e93d0465ac6cf8815c1e5e7635e330a))
* **deps:** pin the gibson images to v0.138.0 ([#150](https://github.com/zeroroot-ai/charts/issues/150)) ([21bd05a](https://github.com/zeroroot-ai/charts/commit/21bd05aed41806853b97d82035e68e6eef1b3e5e))
* **deps:** setec 0.115.0, and the reserved list carries IPv6 ([#143](https://github.com/zeroroot-ai/charts/issues/143)) ([927a4a8](https://github.com/zeroroot-ai/charts/commit/927a4a87ccf5fc5dedc29ba01211eee61b9952fb))
* **docs:** every guard a comment names exists ([#126](https://github.com/zeroroot-ai/charts/issues/126)) ([5eef840](https://github.com/zeroroot-ai/charts/commit/5eef840fe9e9b5048a1f52aebc9d4a7609d016f4))
* **envoy:** the admin interface binds loopback behind a two-route listener ([#139](https://github.com/zeroroot-ai/charts/issues/139)) ([6b91d26](https://github.com/zeroroot-ai/charts/commit/6b91d261565d4c764728216d3b6805c043e916d5))
* **ext-authz:** the native-login (device grant) client is a human sign-in client ([#149](https://github.com/zeroroot-ai/charts/issues/149)) ([b6eadee](https://github.com/zeroroot-ai/charts/commit/b6eadeebfa7421d30a43d40ba0fd35dcf0683476))
* **gibson-workloads:** the daemon's fixture flag follows the e2e runner toggle ([#142](https://github.com/zeroroot-ai/charts/issues/142)) ([9dc8392](https://github.com/zeroroot-ai/charts/commit/9dc83925d15221223b4068de6aa34cf075e84d52))
* **gibson:** the belief sidecar binds loopback and probes exec in-container ([#127](https://github.com/zeroroot-ai/charts/issues/127)) ([7f861fd](https://github.com/zeroroot-ai/charts/commit/7f861fdb9b8ffddeef6c55fed4fe08a6c9f0097e))
* **images:** every mirror image runs by digest ([#140](https://github.com/zeroroot-ai/charts/issues/140)) ([ca7691d](https://github.com/zeroroot-ai/charts/commit/ca7691d8e415f72701e3b3999e3f6608c0f62c1a))
* **images:** pin the dashboard to v0.121.0 ([#130](https://github.com/zeroroot-ai/charts/issues/130)) ([d5d025f](https://github.com/zeroroot-ai/charts/commit/d5d025ff9add8a5654d010de0b8a569b5a561a04))
* **install:** the operators subchart never got the discovered Envoy anchor ([#106](https://github.com/zeroroot-ai/charts/issues/106)) ([ba94742](https://github.com/zeroroot-ai/charts/commit/ba947429bdff6abb4db508a956315e9e9ca26452))
* **jobs:** the postgres setup quotes the password as a sql literal ([#135](https://github.com/zeroroot-ai/charts/issues/135)) ([97bad0b](https://github.com/zeroroot-ai/charts/commit/97bad0b3e1735f30582d2ed8f737f3de0c48cfe2))
* **netpol:** promote the daemon operator ingress rules into the chart ([#153](https://github.com/zeroroot-ai/charts/issues/153)) ([c2c1f13](https://github.com/zeroroot-ai/charts/commit/c2c1f13c0d8277c1032a0a24ffeec9fe10cca00c))
* **netpol:** the dashboard scraper rule admits port 3000 only ([#134](https://github.com/zeroroot-ai/charts/issues/134)) ([a8d0146](https://github.com/zeroroot-ai/charts/commit/a8d014615feacafa591596a0208e8d24fe7a1e80))
* **observability:** the daemon metrics scrape verifies the certificate ([#136](https://github.com/zeroroot-ai/charts/issues/136)) ([0105d8e](https://github.com/zeroroot-ai/charts/commit/0105d8eeb1ddced33827be93dd2ee2cbee2ec1bb))
* point NOTICE at the real vendored-CRD directory ([#103](https://github.com/zeroroot-ai/charts/issues/103)) ([fd97a74](https://github.com/zeroroot-ai/charts/commit/fd97a741e10eeb1014db78cc3da1ddeb7996cd10))
* **rbac:** postgres-exec names its Secrets, grants get only, and is not kept past uninstall ([#112](https://github.com/zeroroot-ai/charts/issues/112)) ([998a6f1](https://github.com/zeroroot-ai/charts/commit/998a6f179994ce44e090056573aaeabec215a425))
* **rbac:** the daemon reads and writes Secrets in tenant namespaces only ([#129](https://github.com/zeroroot-ai/charts/issues/129)) ([a9a7b2a](https://github.com/zeroroot-ai/charts/commit/a9a7b2a9e237441df4e6f6d9932b567892c6cb80))
* **rbac:** the dashboard init containers may read the Secrets they wait on ([#131](https://github.com/zeroroot-ai/charts/issues/131)) ([f5b2f04](https://github.com/zeroroot-ai/charts/commit/f5b2f0494c3402d0d2716e6e361a09a8d22d6c46))
* **rbac:** the dashboard ServiceAccount reads no Secret ([#116](https://github.com/zeroroot-ai/charts/issues/116)) ([beea1d4](https://github.com/zeroroot-ai/charts/commit/beea1d4342a9255560b34599639f158cbba853f8))
* **reaper:** delete the dead search body and keep addresses out of logs ([#133](https://github.com/zeroroot-ai/charts/issues/133)) ([c88e571](https://github.com/zeroroot-ai/charts/commit/c88e571bb90fdb0ca4e79f4192d8bbdb3681a6b8))
* **reaper:** the orphan reaper deletes nothing when it cannot prove who created a user ([#111](https://github.com/zeroroot-ai/charts/issues/111)) ([b7880e4](https://github.com/zeroroot-ai/charts/commit/b7880e4f406c29038a75c3b4c5a7fae00dbba316))
* **reloader:** Reloader reads Secrets in its own namespace only ([#115](https://github.com/zeroroot-ai/charts/issues/115)) ([e73bb87](https://github.com/zeroroot-ai/charts/commit/e73bb879b516aa980227e4361768781a27f6d2f1))
* **rework:** one digest on the kubectl init image ([#141](https://github.com/zeroroot-ai/charts/issues/141)) ([5697d3e](https://github.com/zeroroot-ai/charts/commit/5697d3e564c7cd037d1736bd457c3ea3e4dedad3))
* **rework:** the setec images follow the setec chart to v0.115.0 ([#144](https://github.com/zeroroot-ai/charts/issues/144)) ([d2fc9d2](https://github.com/zeroroot-ai/charts/commit/d2fc9d2cb9aa030e0296c820f7c359e92f328938))
* **scripts:** baseline-set-secret.sh never turns the operator's value into shell text ([#110](https://github.com/zeroroot-ai/charts/issues/110)) ([38c5114](https://github.com/zeroroot-ai/charts/commit/38c51142d78decd59e3c6328f4930e9f4ff46040))
* **security:** the secret scanner reads the goldens ([#138](https://github.com/zeroroot-ai/charts/issues/138)) ([575a269](https://github.com/zeroroot-ai/charts/commit/575a269b747648bb29c01d85bcfd73bbce8d5347))
* **values:** delete the dead allowPublicFallback flag ([#132](https://github.com/zeroroot-ai/charts/issues/132)) ([610acd8](https://github.com/zeroroot-ai/charts/commit/610acd87f068c6edc4774b99786b9b9f0be86e49))
* **values:** no default serving domain ([#137](https://github.com/zeroroot-ai/charts/issues/137)) ([6bb460e](https://github.com/zeroroot-ai/charts/commit/6bb460ee7e08404404956ef664c7c1b1c1d21890))
* **values:** no profile ships a default archive bucket ([#113](https://github.com/zeroroot-ai/charts/issues/113)) ([8288c3c](https://github.com/zeroroot-ai/charts/commit/8288c3cafc1632eb37843e1579d6dd7fed586e87))
* **zitadel:** bump to v4.18.0 ([#146](https://github.com/zeroroot-ai/charts/issues/146)) ([10870a1](https://github.com/zeroroot-ai/charts/commit/10870a174d5be01d8e014751d6d4b393f8809835))

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
