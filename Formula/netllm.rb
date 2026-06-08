class Netllm < Formula
  include Language::Python::Virtualenv

  desc "Mesh router for local LLM backends — swarm agents with OpenAI/Anthropic gateway"
  homepage "https://github.com/matthewdcage/llm-swarm-router"
  url "https://github.com/matthewdcage/llm-swarm-router/archive/refs/tags/v0.2.0.tar.gz"
  sha256 "aaefa7de9a83d7e6cfa14991b0f42463a51bec6db518cae8af7cf3862b0718fc"
  license "MIT"
  head "https://github.com/matthewdcage/llm-swarm-router.git", branch: "main"

  depends_on "python@3.11"
  depends_on "uv"

  def install
    virtualenv_create(libexec, "python3.11")
    ENV["UV_PROJECT_ENVIRONMENT"] = libexec.to_s
    cd buildpath do
      system Formula["uv"].opt_bin/"uv", "sync", "--no-dev",
             "--python", libexec/"bin/python"
    end
    bin.install_symlink libexec/"bin/netllm"
  end

  service do
    run [opt_bin/"netllm", "serve", "-q"]
    keep_alive true
    log_path var/"log/netllm.log"
    error_log_path var/"log/netllm.log"
    environment_variables PATH: std_service_path_env
  end

  test do
    assert_match "netllm", shell_output("#{bin}/netllm --version")
  end
end
