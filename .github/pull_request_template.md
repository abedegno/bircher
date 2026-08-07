<!--
Keep this short. The checklist exists because these are the things that have
actually broken before, not as ceremony.
-->

## What and why

<!-- What changes, and what failure or need prompted it. Link the issue if there is one. -->

## Effect on the merge gate

<!--
Required. Does this change what gets merged, or when? "No effect" is a fine
answer. Anything that could let code merge without a genuine cross-vendor PASS
needs to be called out — that gate is the point of the project.
-->

## Testing

- [ ] `bash batch/run-queue.sh --self-test` passes **on Linux**
- [ ] New behaviour I depend on has a self-test case
- [ ] Where there is a guard, I showed it going red as well as green

<!--
macOS is not enough on its own: it ships BSD sed and bash 3.2, and the difference
is real. A regex using \| alternation once passed on Linux and silently matched
nothing on macOS.
-->

## Anything a reviewer should look at twice

<!-- Assumptions you are not sure about, or code you would like a second opinion on. -->
