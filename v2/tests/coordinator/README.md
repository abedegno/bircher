# No `__init__.py` here, deliberately

`tests/kernel/` has none either, and for the same reason: with one, pytest
roots the package at `tests/` and the directory name becomes an importable
top-level package — so `tests/coordinator` shadows `v2/coordinator` and
`from coordinator.observe import ...` resolves to the test directory, which has
no `observe`.

`tests/execution/` DOES have one, and is fine, because no real package is
called `execution`. The rule is: a test directory may carry `__init__.py` only
if its name is not also a package name under `v2/`.
