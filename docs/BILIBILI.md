# 哔哩哔哩公开视频音频

在搜索框直接粘贴公开视频链接，点击搜索即可得到一条“哔哩哔哩”结果，再点 `+` 点歌。

支持：

```text
https://www.bilibili.com/video/BV...
https://bilibili.com/video/av...
https://b23.tv/...
带 ?p=2 的多 P 链接
```

只做：

- 公开视频
- 纯音频流
- 服务器端代理/ffmpeg 转码
- 浏览器不直连哔哩哔哩
- 不保存第三方音频文件

不做：

- 登录 Cookie
- 会员/付费/DRM
- 直播
- 绕过地区限制
- 视频画面下载

实现：

```text
app/sources/bilibili.py
```

使用标准库访问公开 API：

1. 解析 BV/av 或多 P 编号
2. 获取公开视频信息
3. 获取 DASH `audio` 流
4. 交给服务器 `/native` 和 ffmpeg
5. 播放时带 `Referer: https://www.bilibili.com/`

如果视频没有公开纯音频流、需要登录或已失效，会在搜索状态栏显示错误。

也可以直接输入裸 ID：

```text
BV12w426rEcW
av80433022
```

裸 BV 会被规范化为：

```text
https://www.bilibili.com/video/BV12w426rEcW
```
