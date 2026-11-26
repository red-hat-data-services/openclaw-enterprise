# Podman-only Linux hosts

Run the local `./scripts/k3d` test lanes on a Linux host that has Podman and
no Docker, such as a RHEL or Fedora workstation. The helper selects Podman
through the `configure_podman` path and validates the host before it starts
any cluster work.

The upstream guide already states that native rootless Podman cannot run
this profile; see
[Local Kubernetes development](../../docs/guides/deploy/local-kubernetes-development.md).
This page adds the exact session-level cause of that limit, a preflight that
names each gap, and a rootful route that needs no virtual machine.

## Select the engine

| Setting                                   | Effect                                                                |
| ----------------------------------------- | --------------------------------------------------------------------- |
| `OCC_K3D_CONTAINER_ENGINE=podman-rootful` | Use the system Podman socket. Recommended.                            |
| `OCC_K3D_CONTAINER_ENGINE=podman`         | Use the rootless user socket. Needs the rootless prerequisites below. |
| `OCC_K3D_PODMAN_SOCKET=<path>`            | Use one exact socket, overriding discovery.                           |
| `DOCKER_HOST=unix://<path>`               | Honored when it selects a `unix://` socket.                           |

The helper exports `DOCKER_HOST`, `DOCKER_SOCK` and `CONTAINER_HOST` from the
selected socket. `DOCKER_SOCK` names the socket that k3d mounts into its tools
node, and `CONTAINER_HOST` routes the Podman CLI to the same socket, so image
pulls and builds reach the storage that k3d reads.

## Rootful setup (recommended)

The system Podman socket behaves closest to the Docker daemon that the test
lanes expect: containers run with root cgroups, which provide every
controller, and with the privileges the OCI runtime needs.

Keep the exposure session-scoped. Access to a rootful Podman socket is
root-equivalent without a sudo password for anything that already runs as
your user, the same property as Docker group membership. Start the socket
only while you run lanes:

```bash
sudo systemctl start podman.socket
sudo setfacl -m u:"$(id -un)":x /run/podman
sudo setfacl -m u:"$(id -un)":rw /run/podman/podman.sock
OCC_K3D_CONTAINER_ENGINE=podman-rootful ./scripts/k3d test --harness openclaw
```

The access grants revert at reboot, so the standing exposure is zero until
you grant them again. Use `sudo systemctl enable --now podman.socket` only
when you accept a socket that survives reboots; without the access grant an
enabled socket stays root-only.

## Rootless prerequisites

The rootless user socket works only when the host provides what k3s and the
OCI runtime need. The helper checks each requirement before any cluster work
and prints the exact remediation.

1. **Socket mount.** k3d mounts the socket named by `DOCKER_SOCK` into its
   tools node. Without it, k3d mounts `/var/run/docker.sock`, which a
   rootless session never provides, and cluster creation fails. The helper
   exports `DOCKER_SOCK` for you.
2. **cpuset controller.** k3s requires the `cpuset` cgroup controller.
   Distributions commonly delegate only `cpu`, `io`, `memory` and `pids` to
   user sessions. Enable it once per session:

   ```bash
   echo +cpuset | sudo tee /sys/fs/cgroup/user.slice/cgroup.subtree_control
   echo +cpuset | sudo tee "/sys/fs/cgroup/user.slice/user-$(id -u).slice/cgroup.subtree_control"
   echo +cpuset > "/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.subtree_control"
   echo +cpuset > "/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/user.slice/cgroup.subtree_control"
   ```

   These writes revert at reboot.

3. **oom_score_adj.** An unprivileged session cannot lower `oom_score_adj`
   for containers. When the session inherits a non-zero value, every rootless
   container that requests a lower score fails to start. The value comes from
   the user systemd manager; check it with
   `systemctl show "user@$(id -u).service" -p OOMScoreAdjust`. Either clear
   that value and sign in again, or use the rootful socket.

## What the preflight validates

`preflight_podman_engine` runs when the helper selects Podman on Linux. It
checks, in order: socket accessibility, socket presence, the Podman API
answer, the `cpuset` chain for rootless sockets, and the session
`oom_score_adj`. Each failure stops the helper before any cluster,
database or image work and prints the host command that fixes it.

macOS hosts skip the preflight: podman-machine runs a rootful virtual
machine.
