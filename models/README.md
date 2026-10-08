# Local model

The default model is Qwen2.5 1.5B Instruct Q4_K_M (about 1.12 GB).
Use the launcher's **Download Qwen model** button, or run:

```
python -m pollevbot.model_setup
```

The download comes from the [official Qwen GGUF repository](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF),
which publishes the model under Apache 2.0. The downloader checks SHA256 before
making the file available:

```
6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e
```

`LLM_MODEL_PATH=models/qwen2.5-1.5b-instruct-q4_k_m.gguf` resolves relative to
this project's root, regardless of the terminal's working directory. You can
also choose another GGUF instruct model in the launcher. Model binaries are
ignored by Git; each new installation downloads its own copy.
