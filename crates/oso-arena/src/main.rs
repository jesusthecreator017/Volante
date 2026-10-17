fn main() -> anyhow::Result<()> {
    // Return the error so wgpu failures aren't swallowed silently
    oso_arena::run()
}
