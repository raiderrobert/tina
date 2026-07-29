# Changelog

## 0.1.0 (2026-07-29)


### Features

* add config, outcome models, and structured logging ([a8ed1b3](https://github.com/raiderrobert/tina/commit/a8ed1b3534cead7888c6440a3572ed4bd17bf850))
* add dispatch and run entrypoints ([76d9681](https://github.com/raiderrobert/tina/commit/76d9681a0c5ce7925f37d547cd1dad021011c4a5))
* add jira and github issue source adapters ([732c4ff](https://github.com/raiderrobert/tina/commit/732c4ff26784b2fdf7d65003b8b6e02f9b20c33e))
* add local and cloudrun executors ([db4388e](https://github.com/raiderrobert/tina/commit/db4388e0e44c8eb112b098bb223acb36f71f3900))
* assemble one-shot prompts and invoke the agent harness ([3e34ea4](https://github.com/raiderrobert/tina/commit/3e34ea4625f71f33e0e7ca7fa6c52d827bc17127))
* **cli:** add --dry-run to dispatch ([04a4847](https://github.com/raiderrobert/tina/commit/04a484783db03049bd52394a02b5bce59ed144c9)), closes [#11](https://github.com/raiderrobert/tina/issues/11)
* **cli:** add a --version flag ([55a2190](https://github.com/raiderrobert/tina/commit/55a2190b5ee4c857d616114fe4e39db1c0a1e9e8)), closes [#4](https://github.com/raiderrobert/tina/issues/4)
* **errors:** carry cause and fix, and render them on stderr ([6c86256](https://github.com/raiderrobert/tina/commit/6c8625684cf57b56700743fc05276c45c98f8ebd)), closes [#10](https://github.com/raiderrobert/tina/issues/10)
* verify declared artifacts exist ([10cf137](https://github.com/raiderrobert/tina/commit/10cf13735d73881863244bcbefb17553eec15ea2))


### Bug Fixes

* **tests:** strip ANSI styling before asserting on help output ([4598322](https://github.com/raiderrobert/tina/commit/459832265b94cc779abe94d117783f41a972b639)), closes [#4](https://github.com/raiderrobert/tina/issues/4)


### Documentation

* add a terminal demo recording to the README ([39a09fa](https://github.com/raiderrobert/tina/commit/39a09fa07dcdbe5c7d84c386e3d83d138c78b4ba)), closes [#15](https://github.com/raiderrobert/tina/issues/15)
* add ADRs for factory architecture decisions ([6999f8b](https://github.com/raiderrobert/tina/commit/6999f8b0ac5348d459a65f7908e65792a6b6ffb3))
* add AGENTS.md with CLAUDE.md symlinked to it ([cd7e2a3](https://github.com/raiderrobert/tina/commit/cd7e2a3511ddd1fcb730da836d160a9ed79177c1)), closes [#8](https://github.com/raiderrobert/tina/issues/8)
* add CONTRIBUTING.md and cross-link it from the README ([0f36362](https://github.com/raiderrobert/tina/commit/0f363620985b38f2b580f802f07827a82e84368d)), closes [#5](https://github.com/raiderrobert/tina/issues/5)
* add project README ([d61dc3d](https://github.com/raiderrobert/tina/commit/d61dc3de74e7c446af026681f10117e475bb2f76))
* align config examples with implemented schema ([f01efa4](https://github.com/raiderrobert/tina/commit/f01efa488d138805536943bd71ac620f2391d691))
* autonomous factory architecture ([94cbfa1](https://github.com/raiderrobert/tina/commit/94cbfa10e5711e48026af0e86e843be714315f91))
* autonomous loop design document ([d38117a](https://github.com/raiderrobert/tina/commit/d38117a6bf7ee0c69daa5de69cabe99912151498))
* correct the type-check command to ty ([71a3c36](https://github.com/raiderrobert/tina/commit/71a3c3632cd53f8da36cf634dbcd94237389e004))
* initial architecture, type reference, and ADRs ([0967ab3](https://github.com/raiderrobert/tina/commit/0967ab3769c04547eecf5c4c98187e6ec14be1a9))
* lead README example with the github bug workflow ([b578c64](https://github.com/raiderrobert/tina/commit/b578c647d0c3578b3a187b24503758b253064606))
* remove Jira/GitHub as baked-in sources and tools ([9cba852](https://github.com/raiderrobert/tina/commit/9cba852d2a63498a981d2b54ba99a05e0224fccb))
* replace toolkit design with autonomous factory architecture ([4147996](https://github.com/raiderrobert/tina/commit/4147996321762257b61d63b81b70659ff28a054c))
* rewrite README to match pi mono repo style ([4121177](https://github.com/raiderrobert/tina/commit/4121177d78ce504fba89ce1c4be49211b2b005d9))
* swap Tina quote ([12a759c](https://github.com/raiderrobert/tina/commit/12a759c3bf8a9f17f6913f519062b8abe474bf89))


### Continuous Integration

* add release automation with release-please and PyPI trusted publishing ([bef3a5d](https://github.com/raiderrobert/tina/commit/bef3a5d1c78605b3b3d1eaffc63b85ffa4ebb150)), closes [#2](https://github.com/raiderrobert/tina/issues/2)
