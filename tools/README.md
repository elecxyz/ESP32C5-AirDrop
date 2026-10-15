# Received-file tidy helper

`airdrop-tidy` is inherited from CachyDrop and used by the C5 receiver's
completion-notification hook.

iOS can put received files inside a temporary `NSIRD_AirDrop_*` directory,
alongside AppleDouble `._*` metadata files. The helper removes those sidecars
and `.DS_Store` files, moves the wrapper's contents into the destination
directory and removes the empty wrapper. Sent subdirectories are moved
intact. A filename collision gets a numbered suffix instead of overwriting
an existing file.

```sh
tools/airdrop-tidy [DIR]
tools/airdrop-tidy --watch DIR
```

The destination defaults to `$RECV_DIR`, then `~/Downloads`. The optional
watch mode repeats every `AIRDROP_TIDY_POLL` seconds; it is not an always-on
AirDrop receiver. Desktop integration runs the helper after successful
extraction rather than keeping a watcher alive.

By default, a wrapper must have been unchanged for `AIRDROP_TIDY_SETTLE`
seconds before it is flattened. The completion hook can use a zero settle
time because extraction has already finished. Wrapper removal uses `rmdir`,
so unexpected remaining contents prevent removal instead of being deleted.

This helper **does delete Apple metadata files**, including matching sidecars
at the destination's top level. Run it only on an intended receive directory,
not an arbitrary collection of files whose macOS metadata you want to retain.
An incomplete raw upload is not a successful extraction and should not
trigger the completion hook.
