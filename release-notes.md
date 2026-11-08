# DeepSleep For Windows(Beta) 0.1.1

修好了 0.1.0 里的一个问题。

- 窗口打不开，提示「the window needs pywebview, which is not part of this build」。构建时漏掉了窗口组件，程序启动后找不到它。现在窗口能正常打开了。
- 打包后增加一次自检，缺东西会当场发现，不会等到用户双击才知道。

其余功能与 0.1.0 相同。
