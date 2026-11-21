# Cindy 插件构建

`core/` 是本地生成目录，不提交。公共服务端、界面及技能源码位于上一级目录；`patches/` 只保存 Cindy 特有的差异。

从 0.2.61 起，Cindy、Codex 和 DeepSeekRuntime 使用同一个 release 版本号。发版时同步更新 `ghost.json`、`../.codex-plugin/plugin.json`、`../dsh/package.json`；`prepare.py` 在生成、检查和打包前校验三者一致，版本不同会拒绝构建。

本地运行和测试前生成目录：

```sh
python3 plugins/task-progress/cindy/prepare.py
python3 plugins/task-progress/cindy/prepare.py --check
```

`prepare.py` 会替换整个生成的 `core/`，不要直接在其中修改源码。公共修改应写在上一级，Cindy 特有修改应更新 `patches/`；补丁无法应用时构建会失败，须先重新对照差异。

打包时先创建一个不存在的干净目录，再将该目录交给 Cindy Forge 的 `ghost_forge_pack`：

```sh
python3 plugins/task-progress/cindy/prepare.py --stage "$PWD/.cindy-build/task-progress"
```

Forge 的 `dir` 使用输出的绝对路径。构建目录包含运行所需的完整文件，不包含补丁或其他源码；打包不会安装插件。再次构建请使用新的目录，避免覆盖已有构建产物。
