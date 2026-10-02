# Changelog

## [0.135.4](https://github.com/zeroroot-ai/charts/compare/v0.135.3...v0.135.4) (2026-10-02)


### Bug Fixes

* **auth:** a security bound is not an operator knob ([#323](https://github.com/zeroroot-ai/charts/issues/323)) ([d3ea4be](https://github.com/zeroroot-ai/charts/commit/d3ea4bef953d86c4d2306d918d2e1f986e255055)), closes [#317](https://github.com/zeroroot-ai/charts/issues/317)
* **cg:** render and assert the signing-key rotation window, which no profile shows ([#321](https://github.com/zeroroot-ai/charts/issues/321)) ([7e7443d](https://github.com/zeroroot-ai/charts/commit/7e7443d23d1b9b6b27abccfeecd7c63c723b6e9e)), closes [#316](https://github.com/zeroroot-ai/charts/issues/316)
* **docs:** no comment names a file that is not there, and 92 did ([#322](https://github.com/zeroroot-ai/charts/issues/322)) ([dcdc4e1](https://github.com/zeroroot-ai/charts/commit/dcdc4e136ae394adad2731dde48269b710b2c437))
* **edge:** a rate limiter that renders fine and enforces nothing ([#324](https://github.com/zeroroot-ai/charts/issues/324)) ([aa6b965](https://github.com/zeroroot-ai/charts/commit/aa6b9654bfbb42ab1041badff2f9b4a4bd1f3325))
* **helpers:** a helper's documentation lives beside its define, and 118 headers did not ([#320](https://github.com/zeroroot-ai/charts/issues/320)) ([3f6c59b](https://github.com/zeroroot-ai/charts/commit/3f6c59ba7b1c2deaedf48878479223c235c093f0)), closes [#304](https://github.com/zeroroot-ai/charts/issues/304)
* **probes:** every probe states its timeout, and a guard keeps it that way ([#318](https://github.com/zeroroot-ai/charts/issues/318)) ([7081e86](https://github.com/zeroroot-ai/charts/commit/7081e86654b7fb6646c0cc5bc7da98f336a6fe9e)), closes [#306](https://github.com/zeroroot-ai/charts/issues/306)

## [0.135.3](https://github.com/zeroroot-ai/charts/compare/v0.135.2...v0.135.3) (2026-10-02)


### Bug Fixes

* **mail:** the tenant-operator's credentials are stated, never inferred ([#310](https://github.com/zeroroot-ai/charts/issues/310)) ([b316131](https://github.com/zeroroot-ai/charts/commit/b316131e6c1151aa2c94d579b3ab9e9d00773d17))

## [0.135.2](https://github.com/zeroroot-ai/charts/compare/v0.135.1...v0.135.2) (2026-10-02)


### Bug Fixes

* **mail:** the tenant-operator names the relay, and the chart declares the key ([#309](https://github.com/zeroroot-ai/charts/issues/309)) ([389924a](https://github.com/zeroroot-ai/charts/commit/389924a15c452785a2b89667d5670320ca18342f))
* **openbao:** the health endpoint the whole bringup waits on gets a timeout it can meet ([#307](https://github.com/zeroroot-ai/charts/issues/307)) ([d7b4dcd](https://github.com/zeroroot-ai/charts/commit/d7b4dcddc1c13971109b8114dc52369f19a6d4b9))

## [0.135.1](https://github.com/zeroroot-ai/charts/compare/v0.135.0...v0.135.1) (2026-10-01)


### Bug Fixes

* **env:** an env var a first-party container never reads now fails the build ([#302](https://github.com/zeroroot-ai/charts/issues/302)) ([0f435c7](https://github.com/zeroroot-ai/charts/commit/0f435c753e5c8e76230667230b9f1ba13dd50342)), closes [#294](https://github.com/zeroroot-ai/charts/issues/294)
* **guards:** an invoked Helm define that renders nothing now fails the build ([#297](https://github.com/zeroroot-ai/charts/issues/297)) ([9d02f47](https://github.com/zeroroot-ai/charts/commit/9d02f47b2ad3d07d8a7bc35609859f4f0805b172)), closes [#293](https://github.com/zeroroot-ai/charts/issues/293)
* **observability:** delete the Grafana the chart ships for a Grafana it never ships ([#305](https://github.com/zeroroot-ai/charts/issues/305)) ([cb33fa1](https://github.com/zeroroot-ai/charts/commit/cb33fa1a4b1cc8cda3fc7af05df7c947eeca7b62)), closes [#301](https://github.com/zeroroot-ai/charts/issues/301)
* **rungs:** shrink the zitadel-login request the baseline just gained ([#298](https://github.com/zeroroot-ai/charts/issues/298)) ([7338f8d](https://github.com/zeroroot-ai/charts/commit/7338f8d410c9236ac08d63bc6dbfd3de5f71f549))
* **values:** a declared values key with no consumer now fails the build ([#300](https://github.com/zeroroot-ai/charts/issues/300)) ([bbdc5d9](https://github.com/zeroroot-ai/charts/commit/bbdc5d999632406556fa578c0991802193d716cd)), closes [#292](https://github.com/zeroroot-ai/charts/issues/292)

## [0.135.0](https://github.com/zeroroot-ai/charts/compare/v0.134.1...v0.135.0) (2026-10-01)


### Features

* **airgap:** delete the Big Bang package, move the image list to airgap/ ([#288](https://github.com/zeroroot-ai/charts/issues/288)) ([2fc5440](https://github.com/zeroroot-ai/charts/commit/2fc54401ad2435cade995681afb80c62cff09294))


### Bug Fixes

* **dashboard:** bind the V8 heap ceiling to the container memory limit ([#295](https://github.com/zeroroot-ai/charts/issues/295)) ([548aa16](https://github.com/zeroroot-ai/charts/commit/548aa16c455838306e7455c6d0a70dc73a00bc70))
* **tenant-operator:** SMTP_HOST names the mailpit Service the release renders ([#290](https://github.com/zeroroot-ai/charts/issues/290)) ([dfb14dc](https://github.com/zeroroot-ai/charts/commit/dfb14dccb2cd89081c372c9898c76734301dfd3f)), closes [#114](https://github.com/zeroroot-ai/charts/issues/114)

## [0.134.1](https://github.com/zeroroot-ai/charts/compare/v0.134.0...v0.134.1) (2026-10-01)


### Bug Fixes

* **docs:** combined image pin shape (unblocks version-links fan-out) ([#285](https://github.com/zeroroot-ai/charts/issues/285)) ([e257443](https://github.com/zeroroot-ai/charts/commit/e25744356ecacb836ac59ba3cf12f31a87ee7bc3))
* **images:** pin gibson v0.148.3, WhoAmI reports kind user for a person ([#287](https://github.com/zeroroot-ai/charts/issues/287)) ([ad216d3](https://github.com/zeroroot-ai/charts/commit/ad216d38b6a2d606bcd028d3c9850dcb05e856c4))

## [0.134.0](https://github.com/zeroroot-ai/charts/compare/v0.133.14...v0.134.0) (2026-10-01)


### Features

* **guards:** a client can never supply x-jwt-payload ([#282](https://github.com/zeroroot-ai/charts/issues/282)) ([5216f65](https://github.com/zeroroot-ai/charts/commit/5216f658c5603ef79a971f9cd5411eab38486976))

## [0.133.14](https://github.com/zeroroot-ai/charts/compare/v0.133.13...v0.133.14) (2026-10-01)


### Bug Fixes

* **edge:** verify a Zitadel JWT on the IdentityService route ([#280](https://github.com/zeroroot-ai/charts/issues/280)) ([6788a4f](https://github.com/zeroroot-ai/charts/commit/6788a4f3a039f4c27276ce61a59d19cd9967f63c)), closes [#279](https://github.com/zeroroot-ai/charts/issues/279)

## [0.133.13](https://github.com/zeroroot-ai/charts/compare/v0.133.12...v0.133.13) (2026-10-01)


### Bug Fixes

* **platform-operator:** show Gibson CLI on the device consent page ([#276](https://github.com/zeroroot-ai/charts/issues/276)) ([4cd44a5](https://github.com/zeroroot-ai/charts/commit/4cd44a56d4786259073d5a476a94f6e93341c0f6))

## [0.133.12](https://github.com/zeroroot-ai/charts/compare/v0.133.11...v0.133.12) (2026-10-01)


### Bug Fixes

* **images:** pin gibson v0.148.0, gibson v0.148.0 for the OIDC client display name (gibson[#452](https://github.com/zeroroot-ai/charts/issues/452)) ([#274](https://github.com/zeroroot-ai/charts/issues/274)) ([c273f3b](https://github.com/zeroroot-ai/charts/commit/c273f3bd8f3f42a784f06327298c86a8a24c1e7b))

## [0.133.11](https://github.com/zeroroot-ai/charts/compare/v0.133.10...v0.133.11) (2026-10-01)


### Bug Fixes

* **images:** pin gibson v0.147.0, a created mission is listed before it runs ([#272](https://github.com/zeroroot-ai/charts/issues/272)) ([4f0f762](https://github.com/zeroroot-ai/charts/commit/4f0f762e2ef9b8f7986403003a800c2fbcb21c32))

## [0.133.10](https://github.com/zeroroot-ai/charts/compare/v0.133.9...v0.133.10) (2026-10-01)


### Bug Fixes

* **zitadel:** bump to v4.18.0 ([#183](https://github.com/zeroroot-ai/charts/issues/183)) ([457284d](https://github.com/zeroroot-ai/charts/commit/457284d3fd9bf5cb6ba5a5aeb3e54fdc6d571f04))

## [0.133.9](https://github.com/zeroroot-ai/charts/compare/v0.133.8...v0.133.9) (2026-10-01)


### Bug Fixes

* **images:** pin gibson v0.146.5, CreateMission answers with its creator ([#269](https://github.com/zeroroot-ai/charts/issues/269)) ([963f444](https://github.com/zeroroot-ai/charts/commit/963f444e6e156eb33bf2a52837d20d2b7949b90e))

## [0.133.8](https://github.com/zeroroot-ai/charts/compare/v0.133.7...v0.133.8) (2026-09-30)


### Bug Fixes

* **images:** pin gibson v0.146.4, the platform-operator reads its CR uncached and never loses a status write ([#267](https://github.com/zeroroot-ai/charts/issues/267)) ([7ac230f](https://github.com/zeroroot-ai/charts/commit/7ac230f67b129b9090f0c7fa76d4314cfc072779))

## [0.133.7](https://github.com/zeroroot-ai/charts/compare/v0.133.6...v0.133.7) (2026-09-30)


### Bug Fixes

* **edge:** send the device approval page to Login V2 ([#265](https://github.com/zeroroot-ai/charts/issues/265)) ([0c70810](https://github.com/zeroroot-ai/charts/commit/0c7081048526198d1188daf2e87102efd2e8e0db))

## [0.133.6](https://github.com/zeroroot-ai/charts/compare/v0.133.5...v0.133.6) (2026-09-30)


### Bug Fixes

* **images:** pin docs-site docs-site-v0.6.11, the domain-packs page exists ([#263](https://github.com/zeroroot-ai/charts/issues/263)) ([92d68f9](https://github.com/zeroroot-ai/charts/commit/92d68f9ab18ddeee6aaffbb1fdbc564f86f95642))

## [0.133.5](https://github.com/zeroroot-ai/charts/compare/v0.133.4...v0.133.5) (2026-09-30)


### Bug Fixes

* **images:** pin gibson v0.146.1, the Platform owner reaches system_tenant RPCs through the edge ([#260](https://github.com/zeroroot-ai/charts/issues/260)) ([217f1fe](https://github.com/zeroroot-ai/charts/commit/217f1fe0f60ede8349857ebbe0beb1684fd10db8))

## [0.133.4](https://github.com/zeroroot-ai/charts/compare/v0.133.3...v0.133.4) (2026-09-30)


### Bug Fixes

* **images:** pin dashboard v0.126.0, one tenant resolver and no picker ([b40e006](https://github.com/zeroroot-ai/charts/commit/b40e006439b40da897b940b2323dbd398b51e376))
* **images:** pin dashboard v0.126.0, people named on missions, findings and jobs ([#258](https://github.com/zeroroot-ai/charts/issues/258)) ([b40e006](https://github.com/zeroroot-ai/charts/commit/b40e006439b40da897b940b2323dbd398b51e376))

## [0.133.3](https://github.com/zeroroot-ai/charts/compare/v0.133.2...v0.133.3) (2026-09-30)


### Bug Fixes

* **images:** pin gibson v0.146.0, missions record their creator, ResolveUsers names people ([#256](https://github.com/zeroroot-ai/charts/issues/256)) ([b7af566](https://github.com/zeroroot-ai/charts/commit/b7af5664e68ec05ed1d36319505ad56ec6c25520))

## [0.133.2](https://github.com/zeroroot-ai/charts/compare/v0.133.1...v0.133.2) (2026-09-30)


### Bug Fixes

* **images:** pin gibson v0.145.0, session gates share one FGA call per token ([#254](https://github.com/zeroroot-ai/charts/issues/254)) ([1969021](https://github.com/zeroroot-ai/charts/commit/1969021c463f1b8cc1870ac75f6df9360cb55e5a))

## [0.133.1](https://github.com/zeroroot-ai/charts/compare/v0.133.0...v0.133.1) (2026-09-30)


### Bug Fixes

* **ci:** link-check checks only the Markdown a PR touched (.github v0.7.2) ([#252](https://github.com/zeroroot-ai/charts/issues/252)) ([49db15c](https://github.com/zeroroot-ai/charts/commit/49db15c0a13971f9e46073e82e06796f3c86f490))
* **images:** pin gibson v0.144.0, a role change reaches ext-authz at once ([#251](https://github.com/zeroroot-ai/charts/issues/251)) ([6e3f432](https://github.com/zeroroot-ai/charts/commit/6e3f432a3c90e82ae94deeadf6d6adb749b9e426))

## [0.133.0](https://github.com/zeroroot-ai/charts/compare/v0.132.25...v0.133.0) (2026-09-29)


### Features

* **ext-authz:** the FGA write event feed, so a role change is refused at once ([#249](https://github.com/zeroroot-ai/charts/issues/249)) ([e4a53a7](https://github.com/zeroroot-ai/charts/commit/e4a53a7b836118f4e36e7ce05535e135bf2c9cee))

## [0.132.25](https://github.com/zeroroot-ai/charts/compare/v0.132.24...v0.132.25) (2026-09-29)


### Bug Fixes

* **images:** pin gibson v0.143.8, the FGA cache TTL runs at its 5 s default ([#247](https://github.com/zeroroot-ai/charts/issues/247)) ([1b251db](https://github.com/zeroroot-ai/charts/commit/1b251db3c1e1cf9ead7e5127314efd1e36f12181))

## [0.132.24](https://github.com/zeroroot-ai/charts/compare/v0.132.23...v0.132.24) (2026-09-29)


### Bug Fixes

* **images:** pin dashboard v0.125.0, Editor gates on mission actions ([#245](https://github.com/zeroroot-ai/charts/issues/245)) ([f1fbd56](https://github.com/zeroroot-ai/charts/commit/f1fbd564a13e24f0f499c38456b8f25eda42803b))
* **images:** pin dashboard v0.125.0, one tenant resolver and no picker ([f1fbd56](https://github.com/zeroroot-ai/charts/commit/f1fbd564a13e24f0f499c38456b8f25eda42803b))

## [0.132.23](https://github.com/zeroroot-ai/charts/compare/v0.132.22...v0.132.23) (2026-09-29)


### Bug Fixes

* **images:** pin gibson v0.143.7, sdk v0.183.1, a Viewer never writes ([#243](https://github.com/zeroroot-ai/charts/issues/243)) ([65fd49a](https://github.com/zeroroot-ai/charts/commit/65fd49a0f4dafda501a1322cb0e17fad44a7d1b8))

## [0.132.22](https://github.com/zeroroot-ai/charts/compare/v0.132.21...v0.132.22) (2026-09-29)


### Bug Fixes

* **images:** pin dashboard v0.124.2, one tenant resolver and no picker ([#241](https://github.com/zeroroot-ai/charts/issues/241)) ([4edb76e](https://github.com/zeroroot-ai/charts/commit/4edb76e6cf7043c3527ee9a27206456f74e97418))

## [0.132.21](https://github.com/zeroroot-ai/charts/compare/v0.132.20...v0.132.21) (2026-09-29)


### Bug Fixes

* **images:** pin gibson v0.143.6, the Platform owner becomes ready when its status write races ([#239](https://github.com/zeroroot-ai/charts/issues/239)) ([5a2a007](https://github.com/zeroroot-ai/charts/commit/5a2a0077b6ebc8126bf7d50a960f26f88002c18f))

## [0.132.20](https://github.com/zeroroot-ai/charts/compare/v0.132.19...v0.132.20) (2026-09-29)


### Bug Fixes

* **images:** pin dashboard v0.124.1, a denial from inside an RPC reads as permission denied ([#237](https://github.com/zeroroot-ai/charts/issues/237)) ([85ce048](https://github.com/zeroroot-ai/charts/commit/85ce048a27875bbbe7e9f18a90a35044eeeb753c))

## [0.132.19](https://github.com/zeroroot-ai/charts/compare/v0.132.18...v0.132.19) (2026-09-29)


### Bug Fixes

* **images:** pin gibson v0.143.5, tenant role changes land within seconds ([#235](https://github.com/zeroroot-ai/charts/issues/235)) ([8980eee](https://github.com/zeroroot-ai/charts/commit/8980eee68b0be21085e742918ba74b16da523594))

## [0.132.18](https://github.com/zeroroot-ai/charts/compare/v0.132.17...v0.132.18) (2026-09-29)


### Bug Fixes

* **images:** pin dashboard v0.124.0, Editor on invite and role change and a Viewer gate on the mission editor ([#232](https://github.com/zeroroot-ai/charts/issues/232)) ([b596649](https://github.com/zeroroot-ai/charts/commit/b596649e585b22bb1126e360d1443a0b88081c60))

## [0.132.17](https://github.com/zeroroot-ai/charts/compare/v0.132.16...v0.132.17) (2026-09-29)


### Bug Fixes

* **images:** pin gibson v0.143.4, the mission-draft RPCs gate on writer and all four tenant roles are reported ([#231](https://github.com/zeroroot-ai/charts/issues/231)) ([a39bbc6](https://github.com/zeroroot-ai/charts/commit/a39bbc616479924d755de62e9c6ac9c72ddbe9cb))

## [0.132.16](https://github.com/zeroroot-ai/charts/compare/v0.132.15...v0.132.16) (2026-09-29)


### Bug Fixes

* **edge:** answer a coalesced HTTP/2 request with 421 on both browser-facing chains ([#226](https://github.com/zeroroot-ai/charts/issues/226)) ([a2ba025](https://github.com/zeroroot-ai/charts/commit/a2ba02515d333ddd99fd94654b83d571a0abb872))
* **edge:** the access log keeps the Authorization scheme word, never the token ([#230](https://github.com/zeroroot-ai/charts/issues/230)) ([f7723de](https://github.com/zeroroot-ai/charts/commit/f7723de587f70df1bf5aaf19a2016a292810a64a))

## [0.132.15](https://github.com/zeroroot-ai/charts/compare/v0.132.14...v0.132.15) (2026-09-29)


### Bug Fixes

* **images:** pin gibson v0.143.3, MFA reset audit record and provisioned-tenant owner ([#227](https://github.com/zeroroot-ai/charts/issues/227)) ([d095b3e](https://github.com/zeroroot-ai/charts/commit/d095b3e60f8b054600f9a9bca6b96448f7749fab))

## [0.132.14](https://github.com/zeroroot-ai/charts/compare/v0.132.13...v0.132.14) (2026-09-29)


### Bug Fixes

* **images:** pin dashboard v0.123.1, one membership verdict per person instead of one daemon call per request ([#224](https://github.com/zeroroot-ai/charts/issues/224)) ([20643b7](https://github.com/zeroroot-ai/charts/commit/20643b7368e18a55386c52127418944d636927b1))

## [0.132.13](https://github.com/zeroroot-ai/charts/compare/v0.132.12...v0.132.13) (2026-09-29)


### Bug Fixes

* **baseline-verify:** wait for the recreated OpenBao pod before waiting for Ready ([#222](https://github.com/zeroroot-ai/charts/issues/222)) ([c71b559](https://github.com/zeroroot-ai/charts/commit/c71b559aef34efc66a34d2fd8b4693a4bccf465d))

## [0.132.12](https://github.com/zeroroot-ai/charts/compare/v0.132.11...v0.132.12) (2026-09-29)


### Bug Fixes

* **baseline:** use localhost.zeroroot.ai instead of selfhosted.example.com ([#218](https://github.com/zeroroot-ai/charts/issues/218)) ([b84a713](https://github.com/zeroroot-ai/charts/commit/b84a713bb452629814b9a99981651cedb4e68953))
* **envoy:** stop the browser WAF from reading first-party session cookies and gRPC-web bodies as attacks ([#220](https://github.com/zeroroot-ai/charts/issues/220)) ([dd6ab80](https://github.com/zeroroot-ai/charts/commit/dd6ab80bc9579c10e82f41d868a1774f93375ac4))

## [0.132.11](https://github.com/zeroroot-ai/charts/compare/v0.132.10...v0.132.11) (2026-09-29)


### Bug Fixes

* **images:** pin dashboard v0.123.0 for the intelligence-layer queue UIs ([#216](https://github.com/zeroroot-ai/charts/issues/216)) ([e14c422](https://github.com/zeroroot-ai/charts/commit/e14c422e0ab458c1a161ced9b941377f0bdaa32e))

## [0.132.10](https://github.com/zeroroot-ai/charts/compare/v0.132.9...v0.132.10) (2026-09-29)


### Bug Fixes

* **images:** pin gibson-belief-sidecar sha-f1b4f0b for the intelligence layer ([#214](https://github.com/zeroroot-ai/charts/issues/214)) ([a97e0e9](https://github.com/zeroroot-ai/charts/commit/a97e0e91896a8ffab0b625b447be8e1b502c44e3))

## [0.132.9](https://github.com/zeroroot-ai/charts/compare/v0.132.8...v0.132.9) (2026-09-29)


### Bug Fixes

* **images:** pin gibson v0.143.1, a conditioned tuple update reaches OpenFGA as two writes ([#212](https://github.com/zeroroot-ai/charts/issues/212)) ([706b326](https://github.com/zeroroot-ai/charts/commit/706b326a64d7604815829bc097c4698325501903))

## [0.132.8](https://github.com/zeroroot-ai/charts/compare/v0.132.7...v0.132.8) (2026-09-28)


### Bug Fixes

* **images:** pin gibson v0.142.7, an MFA reset refuses the target's old tokens ([#210](https://github.com/zeroroot-ai/charts/issues/210)) ([c9cda24](https://github.com/zeroroot-ai/charts/commit/c9cda242eafe79496509413f30403c25da176c54))

## [0.132.7](https://github.com/zeroroot-ai/charts/compare/v0.132.6...v0.132.7) (2026-09-28)


### Bug Fixes

* **images:** pin gibson v0.142.6, MFA reset and profile calls reach users in any org ([#208](https://github.com/zeroroot-ai/charts/issues/208)) ([b5fbeb6](https://github.com/zeroroot-ai/charts/commit/b5fbeb6c19aa2a72c5196b07295fec9ca49c39f0))

## [0.132.6](https://github.com/zeroroot-ai/charts/compare/v0.132.5...v0.132.6) (2026-09-28)


### Bug Fixes

* **images:** pin gibson v0.142.5, an accepted invitee gets session records ([#206](https://github.com/zeroroot-ai/charts/issues/206)) ([e64bc64](https://github.com/zeroroot-ai/charts/commit/e64bc64249ea28ab24fa7f5d7576a3a6a68565c3))

## [0.132.5](https://github.com/zeroroot-ai/charts/compare/v0.132.4...v0.132.5) (2026-09-28)


### Bug Fixes

* **edge:** an invitee can accept an invitation without a session ([#202](https://github.com/zeroroot-ai/charts/issues/202)) ([7a01369](https://github.com/zeroroot-ai/charts/commit/7a0136954f3a56c0cdaf41b7af2ab28a2ddd2348))
* **images:** pin gibson v0.142.4, invitees are created as active users ([#205](https://github.com/zeroroot-ai/charts/issues/205)) ([e8c3409](https://github.com/zeroroot-ai/charts/commit/e8c34099046bb9bd7430228653b4e431192b26c0))
* **pins:** dashboard v0.122.2, the event stream releases its daemon subscription ([#199](https://github.com/zeroroot-ai/charts/issues/199)) ([8379fc5](https://github.com/zeroroot-ai/charts/commit/8379fc533d00a7dbe6c587068b56dd6274c0447a))
* **pins:** dashboard v0.122.3, the membership lookup no longer recurses into itself ([#201](https://github.com/zeroroot-ai/charts/issues/201)) ([17a3d7b](https://github.com/zeroroot-ai/charts/commit/17a3d7b0b41eb98e75a2306709a5d37894cbb990))
* **pins:** dashboard v0.122.4, authorization understands self-mode RPCs ([#204](https://github.com/zeroroot-ai/charts/issues/204)) ([8548235](https://github.com/zeroroot-ai/charts/commit/85482355ebbdd6b2b54153d40321161bca38327c))


### Reverts

* **edge:** drop the anonymous AcceptInvitation rule, no real path needs it ([#203](https://github.com/zeroroot-ai/charts/issues/203)) ([5afa100](https://github.com/zeroroot-ai/charts/commit/5afa100370c730aadb258fc3c07620d1d132ffd4))

## [0.132.4](https://github.com/zeroroot-ai/charts/compare/v0.132.3...v0.132.4) (2026-09-28)


### Bug Fixes

* **pins:** dashboard v0.122.1, the transport stops leaking HTTP/2 sessions ([#197](https://github.com/zeroroot-ai/charts/issues/197)) ([c6b06ab](https://github.com/zeroroot-ai/charts/commit/c6b06abee3f9d5253faf2da2984e0837dd2e905c))

## [0.132.3](https://github.com/zeroroot-ai/charts/compare/v0.132.2...v0.132.3) (2026-09-27)


### Bug Fixes

* **dashboard:** delete the shell-user GC CronJob, which could never run ([#195](https://github.com/zeroroot-ai/charts/issues/195)) ([3de88ca](https://github.com/zeroroot-ai/charts/commit/3de88ca6d7f383cefb81f1bb121e973be7f740c2))

## [0.132.2](https://github.com/zeroroot-ai/charts/compare/v0.132.1...v0.132.2) (2026-09-27)


### Bug Fixes

* **fga-init:** remove platform_operator holders outside the required list ([#193](https://github.com/zeroroot-ai/charts/issues/193)) ([c9c25b8](https://github.com/zeroroot-ai/charts/commit/c9c25b8b9cb60689950951aaf80a4a1c95e84ea3))
* **images:** pin gibson v0.142.3 ([#194](https://github.com/zeroroot-ai/charts/issues/194)) ([9d9c496](https://github.com/zeroroot-ai/charts/commit/9d9c496f0958e395e1fd6545b2a1723915f6df51))
* **velero:** the Schedule and storage location are ordinary resources, so a bump reaches Argo ([#191](https://github.com/zeroroot-ai/charts/issues/191)) ([469bc06](https://github.com/zeroroot-ai/charts/commit/469bc067bb2a97c60dc9f0ff011b720c666bd89f))

## [0.132.1](https://github.com/zeroroot-ai/charts/compare/v0.132.0...v0.132.1) (2026-09-27)


### Bug Fixes

* **netpol:** every NetworkPolicy applies before the hook Jobs ([#188](https://github.com/zeroroot-ai/charts/issues/188)) ([0c00d52](https://github.com/zeroroot-ai/charts/commit/0c00d5297f623976813677819c9a8e077fd81ff3))

## [0.132.0](https://github.com/zeroroot-ai/charts/compare/v0.131.0...v0.132.0) (2026-09-27)


### Features

* **chart:** give Zitadel an active SMTP email provider ([#179](https://github.com/zeroroot-ai/charts/issues/179)) ([550eb5d](https://github.com/zeroroot-ai/charts/commit/550eb5dae6640ee1f6a6040f5802670d0ab26328))
* **chart:** the edge refuses email and username changes and lists every Zitadel route ([#177](https://github.com/zeroroot-ai/charts/issues/177)) ([62090b7](https://github.com/zeroroot-ai/charts/commit/62090b7283e1e4d3907b1d27bfcde3972895d925))
* **chart:** the first tenant's Owner gets a setup link; gibson v0.142.0 and dashboard v0.122.0 ([#174](https://github.com/zeroroot-ai/charts/issues/174)) ([6604b69](https://github.com/zeroroot-ai/charts/commit/6604b690135ed8757df960a18ea70543c8d69a69))


### Bug Fixes

* **acceptance:** the admin-role live check runs against a real cluster ([#185](https://github.com/zeroroot-ai/charts/issues/185)) ([a5830ca](https://github.com/zeroroot-ai/charts/commit/a5830ca010184989c98288e92e4b143a00bf576d))
* **gibson:** guard against an instance-administrator role leaking back in ([#182](https://github.com/zeroroot-ai/charts/issues/182)) ([9f34d52](https://github.com/zeroroot-ai/charts/commit/9f34d5276cffc7bd8a462ccc21185af11bd9f070))
* **gibson:** narrow the daemon and tenant-operator to IAM_ORG_MANAGER ([#176](https://github.com/zeroroot-ai/charts/issues/176)) ([cf423c4](https://github.com/zeroroot-ai/charts/commit/cf423c441913548ea3fd06e6b0cfb3f3a56e40b2))
* **rbac:** no workload can exec into, patch, mint a token for or bind over an owner-credential reader ([#178](https://github.com/zeroroot-ai/charts/issues/178)) ([012ca4d](https://github.com/zeroroot-ai/charts/commit/012ca4d833a94d8be7622a68b113b5dea523dc7b))
* **tenant:** size the enterprise-deploy tenant Neo4j at the measured floor ([#181](https://github.com/zeroroot-ai/charts/issues/181)) ([2febe08](https://github.com/zeroroot-ai/charts/commit/2febe083188a65fbbb3a978cd54774212de0556f))
* **velero:** skip the k3s install kinds so a k3s backup completes ([#186](https://github.com/zeroroot-ai/charts/issues/186)) ([d8433e3](https://github.com/zeroroot-ai/charts/commit/d8433e39c81096a03561bec95b0aa35060a20c6d))

## [0.131.0](https://github.com/zeroroot-ai/charts/compare/v0.130.3...v0.131.0) (2026-09-27)


### Features

* **chart:** give the tenant-operator GIBSON_APP_URL so invitation and welcome links reach a human ([#172](https://github.com/zeroroot-ai/charts/issues/172)) ([32bf9d5](https://github.com/zeroroot-ai/charts/commit/32bf9d55d7abea285521272cacc9c38c290d1c43))
* **chart:** require and validate the Platform owner install values ([#169](https://github.com/zeroroot-ai/charts/issues/169)) ([6bf850b](https://github.com/zeroroot-ai/charts/commit/6bf850b7056c8b5b46fe0187766b111158a7f708))


### Bug Fixes

* **gibson:** gibson v0.140.0 with the Platform owner, role sync and scoped operator credentials ([#173](https://github.com/zeroroot-ai/charts/issues/173)) ([91d207f](https://github.com/zeroroot-ai/charts/commit/91d207fba578f46af8dbbf5022db9bb7cfa531fd))

## [0.130.3](https://github.com/zeroroot-ai/charts/compare/v0.130.2...v0.130.3) (2026-09-26)


### Bug Fixes

* **gibson:** gibson v0.139.0 with exact service account roles and the sign-in policy ([#170](https://github.com/zeroroot-ai/charts/issues/170)) ([5723240](https://github.com/zeroroot-ai/charts/commit/572324082b004f5769c3c501d771d81724c6b455))
* **zitadel:** bump to v4.18.0 ([#166](https://github.com/zeroroot-ai/charts/issues/166)) ([3f4ba3e](https://github.com/zeroroot-ai/charts/commit/3f4ba3e461fa0b6cd14165ad9077c658c2c0f12f))
* **zitadel:** only bootstrap reads the Zitadel owner credentials ([#168](https://github.com/zeroroot-ai/charts/issues/168)) ([3eb83db](https://github.com/zeroroot-ai/charts/commit/3eb83db7facb322254628dd1054b271239364ce2))

## [0.130.2](https://github.com/zeroroot-ai/charts/compare/v0.130.1...v0.130.2) (2026-09-24)


### Bug Fixes

* **edge:** the Envoy edge never forwards a client's instance-selection headers ([#164](https://github.com/zeroroot-ai/charts/issues/164)) ([22921f1](https://github.com/zeroroot-ai/charts/commit/22921f180ec46a38faca472756ba9d1a4925b7b2))
* **idp:** the daemon and first-admin Job reach Zitadel by Service name ([#165](https://github.com/zeroroot-ai/charts/issues/165)) ([f9a67ba](https://github.com/zeroroot-ai/charts/commit/f9a67ba5a40e057d6c93ca2bb9501d15530fbe22))
* **rework:** the velero render passes its installer inputs too ([#158](https://github.com/zeroroot-ai/charts/issues/158)) ([4404770](https://github.com/zeroroot-ai/charts/commit/4404770f89db30b5e6c2ac958551d5b25b3c66de))

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
