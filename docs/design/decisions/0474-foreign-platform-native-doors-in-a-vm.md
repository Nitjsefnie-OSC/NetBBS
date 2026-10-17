# Issue #474 — foreign-platform native doors in a VM — closed

A decision recorded in [§16](../16-decisions.md) of the [NetBBS design](../../NetBBS-design-doc.md).

A door that exists only for another platform -- the motivating case was
`aempire`, a statically linked Linux x86_64 Free Pascal door with no NetBSD
build and no source -- runs under `adapter: "vm"`: one qemu guest per caller,
booted from a kernel and initramfs the SysOp builds. The Phase 7 bullet above
states the runtime contract; the door guide's "Foreign-platform doors in a VM"
states the guest image contract and the manual setup.

**Measured, not assumed.** The scoping questions were answered on the
production host that motivated it (NetBSD 11.0 amd64, itself a VMware guest,
qemu 11.1.1 from pkgsrc):

| Question | Answer |
| --- | --- |
| Q1: does 9p `local` work on a NetBSD host? | Yes. Both exports mount (`9p2000.L`, `cache=none`); guest and host each see the other's writes. The persistent installation stays a directory, so backup and "install with ordinary file tools" are unchanged. |
| Q2: does `-chardev socket,fd=N` accept an inherited descriptor? | Yes, for the door's terminal and for a second socketpair carrying QMP. |
| Q3: qemu's address space? | About twice guest RAM plus 500 MiB: a 256 MiB guest fails at 768 MiB and starts at 1000; with `tb-size=64`, steady VSZ ~500 MiB. Validation requires `memory_mb >= 2 * guest_memory_mb + 512`. |
| Q4: cold boot? | 3.65 s ±0.05 to the door's first byte (microvm, PVH direct kernel boot, TCG), 3.7-4.0 s through the full runtime. `pc` with PVH: 6.4 s; `pc` with a bzImage: 7.7 s. |
| Q5: grant `/dev/nvmm`? | The operator accepted it (2026-09-12). Moot on this host: `nvmm` refuses to load there (`cpu not supported`), so everything above is software emulation (TCG). |

**Adapter, not runner.** A `runner` script could spawn qemu, but host
placeholders mean nothing inside a guest, and only a command line NetBBS
builds can *guarantee* `-nic none`, `-nodefaults`, `-no-user-config`, no
display and exactly two exports. `vm.py` owns the argv the way `dosbox.py`
owns `dosbox.conf`; the profile supplies paths, the guest command, the
accelerator and sizes, never qemu flags.

**Per-session, at 3.7 s.** The rescoping comment on the issue put the
threshold at ~3 s for per-session and ~5 s for a long-lived guest serving
callers as a door service (#466). The measurement fell in between, and
per-session was chosen: it keeps isolation per caller rather than per door,
needs no multiplexing inside the guest, confines a guest crash to one
caller, and reuses the existing reap-the-group lifecycle unchanged. A
caller waits about as long as a DOSBox-X start. The measurement is on the
slowest configuration there is -- nested virtualization with no hardware
acceleration -- so the choice only gets better where `nvmm` or `kvm` works.
A VM-backed door service remains possible later if a busy node needs it.

**Channel.** The door's existing socketpair becomes a virtio console
(`virtconsole`, `/dev/hvc0` in the guest): the tty lives in the guest, so
the host PTY's edge cases do not apply, and the door gets a real terminal.
vsock (Linux-only host support), SSH into the guest (needs guest networking
and a credential) and a bespoke multiplexer were rejected, as scoped.

**Exit status and stop.** qemu exits 0 whatever the game did, so the guest
writes the door's status to `exit.status` in the node export; missing or
unreadable is a failure, and `success_exit_codes` maps normal returns, as
`EXIT.ERR`/`RETURN.OK` do for DOS. The guest touches `booted` once its door
is about to start, which bounds a guest that cannot boot by
`boot_timeout_seconds` instead of the caller's time limit. qemu exits on
SIGTERM without informing its guest, so every stop path first presses the
guest's ACPI power button over a private QMP socketpair (the guest hangs
the door up and powers off), then signals the process group as for any door.
qemu stays in that group, so the kill still guarantees no guest outlives
its session.

**What the boundary buys.** A guest cannot read the node's database, keys,
configuration, other doors or anything outside its two exports, and has no
network -- strictly more than a native door. It is still the SysOp's
boundary: the installation export is writable, qemu runs as the service
account, the guest image and its patching are the SysOp's, and an
accelerator is a host-wide privilege grant. Documentation says "an isolation
boundary you provision and own", never "sandboxed doors".

**No shipped image.** NetBBS ships a build recipe
(`examples/doors/vm/build-alpine-guest.sh`, from Alpine's own packages) and
the reference init, not an image whose security updates it would own
forever. The capability probe boots the SysOp's own image with a NetBBS
fixture script and checks the console in both directions with CP437, a
write through the installation export and the exit-status handshake. It can
only be operator-run: unlike the DOS probe, it needs an image that no CI
carries. The runtime contract is instead tested against a fake qemu that
plays the guest's half over the descriptors it is given.

**Deliberately not built.** Resize forwarding into the guest (the console's
geometry is fixed at launch), guests for other architectures (the adapter
is `qemu-system-x86_64` and `microvm`), and the sibling `ssh-exec` adapter
for running a door on a second machine where it is native.
