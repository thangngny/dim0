{ pkgs }: {
  deps = [
    # Python runtime + uv (fast deps)
    pkgs.python313
    pkgs.uv
    # Node for webui build + claude CLI
    pkgs.nodejs_22
    pkgs.nodePackages.npm
    # Build toolchain for native python wheels (asyncpg etc.)
    pkgs.gcc
    pkgs.postgresql.lib
    pkgs.openssl
    pkgs.libffi
    # Misc
    pkgs.ripgrep
    pkgs.curl
    pkgs.bash
  ];
}