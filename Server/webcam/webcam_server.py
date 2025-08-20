'''_webcam服务器'''
import asyncio
import gc
import json
import platform
import argparse
import ssl
import os
import sys
import aiohttp_cors

import aiortc.codecs.h264
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription,RTCConfiguration, RTCIceServer, RTCBundlePolicy
from aiortc.contrib.media import MediaPlayer, MediaRelay
from aiortc.rtcrtpsender import RTCRtpSender

# ---- Monkey patch aiortc, 禁用远端 REMB 并强制固定码率 ----
from aiortc import rtcrtpsender
from aiortc.rtp import RtcpPsfbPacket, RTCP_PSFB_APP

from gros.fupl.handler.webcam.audio_stream import AudioStream
from gros.fupl.handler.webcam.speaker_playback import SpeakerPlayback,AudioReceiverTrack
from gros.fupl.handler.webcam.video_flip_track import VideoFlipTrack
from gros.fupl.config.logger import get_logger

# 获取日志记录器
logger = get_logger(__name__)

_original_handle_rtcp_packet = rtcrtpsender.RTCRtpSender._handle_rtcp_packet

async def _patched_handle_rtcp_packet(self, packet):
    log_debug = getattr(self, "_RTCRtpSender__log_debug", None)
    encoder = getattr(self, "_RTCRtpSender__encoder", None)

    if isinstance(packet, RtcpPsfbPacket) and packet.fmt == RTCP_PSFB_APP:
        if callable(log_debug):
            log_debug("- 忽略 REMB，保持本地固定码率")
        if encoder and hasattr(encoder, "target_bitrate"):
            encoder.target_bitrate = 15000000  # 固定 15 Mbps
        return

    result = await _original_handle_rtcp_packet(self, packet)

    if encoder and hasattr(encoder, "target_bitrate"):
        encoder.target_bitrate = 15000000
        if callable(log_debug):
            log_debug(f"- 已强制码率 {encoder.target_bitrate} bps")

    return result

rtcrtpsender.RTCRtpSender._handle_rtcp_packet = _patched_handle_rtcp_packet
# -------------------------------------------------------------

# Override bitrate parameters of h264
aiortc.codecs.h264.MIN_BITRATE = 3_000_000
aiortc.codecs.h264.MAX_BITRATE = 15_000_000

class WebcamServer:
    """_webcam服务器"""

    def __init__(self, config):
        """初始化"""
        self._pcs = set()
        self._config = config
        self._running = False
        # 注意：如果你在特殊环境下调用 get_event_loop() 可能需要改成新事件循环
        self._main_loop = asyncio.get_event_loop()

        self._relay = None
        self._video_player = None
        self._audio_player = None

        self._speaker_playback = SpeakerPlayback(config["webcam"]["audio"])
        if platform.system() == "Linux":
            self._audio_capture = AudioStream(config["webcam"]["audio"])
        else:
            self._audio_capture = None

        parser = argparse.ArgumentParser(description="WebRTC webcam server")
        parser.add_argument(
            "--cert-file", help="SSL certificate file (for HTTPS)")
        parser.add_argument("--key-file", help="SSL key file (for HTTPS)")
        parser.add_argument(
            "--play-from", help="Read the media from a file and sent it.")
        parser.add_argument(
            "--play-without-decoding",
            help=(
                "Read the media without decoding it (experimental). "
                "For now it only works with an MPEGTS container with only H.264 video."
            ),
            action="store_true",
        )
        parser.add_argument(
            "--host", default=self._config["webcam"]["host"], help="Host for HTTP server (default: 0.0.0.0)"
        )
        parser.add_argument(
            "--port", type=int, default=self._config["webcam"]["port"], help="Port for HTTP server (default: 8080)"
        )
        parser.add_argument("--verbose", "-v", action="count")
        parser.add_argument(
            "--audio-codec", default=self._config["webcam"]["audio"]["audio_codec"], help="Force a specific audio codec (e.g. audio/opus)"

        )
        parser.add_argument(
            "--video-codec", default=self._config["webcam"]["video"]["video_codec"], help="Force a specific video codec (e.g. video/H264)"
        )

        self._args = parser.parse_args()

        # 更安全的 SSL context 创建（如果传入证书）
        if self._args.cert_file:
            try:
                self._ssl_context = ssl.create_default_context(
                    ssl.Purpose.CLIENT_AUTH)
                self._ssl_context.load_cert_chain(
                    self._args.cert_file, self._args.key_file)
            except Exception as e:
                logger.info("Error in create_default_context: %s", e)

                # 回退到简单的 context（尽可能避免程序崩溃）
                try:
                    self._ssl_context = ssl.SSLContext()
                    self._ssl_context.load_cert_chain(
                        self._args.cert_file, self._args.key_file)
                except Exception as ex:
                    logger.info("Error in create_default_context: %s", ex)
                    self._ssl_context = None
        else:
            self._ssl_context = None

        self._app = web.Application()
        cors = aiohttp_cors.setup(self._app, defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods="*",
            )
        })
        self._app.on_shutdown.append(self._on_shutdown)
        cors.add(self._app.router.add_post("/offer", self._offer))
        cors.add(self._app.router.add_get("/", self._index))
        cors.add(self._app.router.add_get("/client.js", self._javascript))

    def run(self):
        """运行服务器"""
        async def start_server():
            runner = web.AppRunner(self._app)
            await runner.setup()
            site = web.TCPSite(runner, host=self._args.host,
                               port=self._args.port, 
                               ssl_context=self._ssl_context,
                               reuse_port=(sys.platform != "win32"))
            await site.start()
            self._running = True
            try:
                # Keep the server running
                while self._running:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                pass
            finally:
                await runner.cleanup()

        # 在后台创建任务启动服务
        asyncio.create_task(start_server())
        
    def stop(self):
        """停止服务器"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            asyncio.create_task(self._on_shutdown(self._app))
        else:
            # 如果 loop 没有运行，这里同步调用也可能需要 await，但保持兼容性
            try:
                loop.run_until_complete(self._on_shutdown(self._app))
            except Exception as e:
                logger.info("Error in _on_shutdown: %s", e)

    async def _offer(self, request):
        """处理WebRTC offer请求"""
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        # pc = RTCPeerConnection()
        pc = RTCPeerConnection(
            configuration=RTCConfiguration(
                iceServers=[RTCIceServer(urls=["stun:stun.l.google.com:19302"])],
                bundlePolicy=RTCBundlePolicy.MAX_BUNDLE
            )
        )
        self._pcs.add(pc)

        @pc.on("track")
        def on_track(track):
            logger.info("Incoming track %s", track.kind)

            # 确保每个 pc 都有自己的任务集合
            if not hasattr(pc, "_tasks"):
                pc._tasks = set()

            # 远端发来音频时，尝试使用 AudioReceiverTrack 做播放处理
            if track.kind == "audio":
                ar = AudioReceiverTrack(track, self._speaker_playback)

                async def drain():
                    while True:
                        try:
                            await ar.recv()
                        except Exception as e:
                            logger.info("Error in drain: %s", e)

                            break
                task = asyncio.create_task(drain())
                pc._tasks.add(task)

        # @pc.on("iceconnectionstatechange")
        # async def on_ice_state_change():
        #     logger.info(f"ICE connection state is {pc.iceConnectionState}")
        #     if pc.iceConnectionState == "connected":
        #         await print_ice_details()


        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info("Connection state is %s", pc.connectionState)
            if pc.connectionState in ["failed", "disconnected", "closed"]:
                if hasattr(pc, "_tasks"):
                    for task in pc._tasks:
                        task.cancel()
                    pc._tasks.clear()

                # 停止所有发送器的 track（稳健处理）
                for sender in pc.getSenders():
                    try:
                        if hasattr(sender, "track") and sender.track:
                            sender.track.stop()
                    except Exception as e:
                        logger.info("Error in on_connectionstatechange: %s", e)

                # 停止所有 transceiver（某些版本没有 stop）
                for transceiver in pc.getTransceivers():
                    try:
                        if hasattr(transceiver, "stop") and callable(transceiver.stop):
                            await transceiver.stop()
                    except Exception as e:
                        logger.info("Error in on_connectionstatechange: %s", e)

                # 关闭 PeerConnection
                try:
                    await pc.close()
                except Exception as e:
                    logger.info("Error in on_connectionstatechange: %s", e)

                # 从集合中移除
                self._pcs.discard(pc)

                # 强制垃圾回收（可选）
                try:
                    gc.collect()
                except Exception as e:
                    logger.info("Error in on_connectionstatechange: %s", e)
            # elif pc.connectionState == "connected":
            #     await print_ice_details()

        # async def print_ice_details():
        #     stats = await pc.getStats()
        #     for report in stats.values():
        #         if report.type == "candidate-pair" and getattr(report, "nominated", False):
        #             proto = getattr(report.localCandidate, "protocol", "").upper()
        #             local_type = getattr(report.localCandidate, "candidateType", "")
        #             remote_type = getattr(report.remoteCandidate, "candidateType", "")
        #             rtt_ms = getattr(report, "currentRoundTripTime", 0) * 1000
        #             logger.info(f"[WebRTC] ICE: {proto} {local_type} -> {remote_type}, RTT={rtt_ms:.0f} ms")


        # 1️⃣ 先设置远端描述（这样 _offerDirection 会被填好）
        await pc.setRemoteDescription(offer)

        # 2️⃣ 再创建本地轨道
        audio, video = self._create_local_tracks(
            self._args.play_from,
            decode=not self._args.play_without_decoding
        )

        if audio:
            audio_sender = pc.addTrack(audio)
            if self._args.audio_codec:
                self._force_codec(pc, audio_sender, self._args.audio_codec)

        if video:
            video_sender = pc.addTrack(video)
            if self._args.video_codec:
                self._force_codec(pc, video_sender, self._args.video_codec)

        # 3️⃣ 遍历 transceivers，只有非 None 才设置方向
        for t in pc.getTransceivers():
            logger.info("Transceiver kind: %s, direction: %s", t.kind, t.direction)
            if t.kind == "video":
                t.direction = "sendonly"
            if t.direction is None:
                t.direction = "sendrecv" if t.kind == "audio" else "sendonly"
            if t._offerDirection is None:
                # 防御性处理，确保不会报错
                t._offerDirection = t.direction

        # 4️⃣ 创建并设置本地描述
        answer = await pc.createAnswer()

        await pc.setLocalDescription(answer)

        # async def force_bitrate(sender, bitrate):
        #     while True:
        #         encoder = getattr(sender, "_RTCRtpSender__encoder", None)
        #         if encoder and hasattr(encoder, "target_bitrate"):
        #             encoder.target_bitrate = bitrate
        #         await asyncio.sleep(0.5)  # 不断覆盖


        # for sender in pc.getSenders():
        #     if sender.kind == "video":
        #         asyncio.create_task(force_bitrate(sender, 10_000_000))

        # # 启动实时码率监控
        # async def log_stats():
        #     last_bytes_sent = 0
        #     last_timestamp = None
        #     while True:
        #         try:
        #             stats = await pc.getStats()
        #             for report in stats.values():
        #                 if report.type == "outbound-rtp" and report.kind == "video":
        #                     bytes_sent = report.bytesSent
        #                     timestamp = report.timestamp
        #                     if last_timestamp is not None:
        #                         delta = (timestamp - last_timestamp)
        #                         delta_sec = delta.total_seconds() if hasattr(delta, "total_seconds") else delta / 1000
        #                         bitrate_bps = (bytes_sent - last_bytes_sent) * 8 / delta_sec
        #                         logger.info(f"[WebRTC] Video bitrate: {bitrate_bps/1e6:.2f} Mbps")
        #                     last_bytes_sent = bytes_sent
        #                     last_timestamp = timestamp

        #                 if report.type == "remote-inbound-rtp" and report.kind == "video":
        #                     logger.info(f"[WebRTC] Packets lost: {report.packetsLost}")

        #         except Exception as e:
        #             logger.warning(f"[WebRTC] Stats error: {e}")
        #             break
        #         await asyncio.sleep(2)

        # asyncio.create_task(log_stats())

        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
            ),
        )

    async def _index(self, request):
        content = open(os.path.join(
            f"{os.getcwd()}/assets/webrtc", "index.html"), "r").read()
        return web.Response(content_type="text/html", text=content)

    async def _javascript(self, request):
        content = open(os.path.join(
            f"{os.getcwd()}/assets/webrtc", "client.js"), "r").read()
        return web.Response(content_type="application/javascript", text=content)

    async def _on_shutdown(self, app) -> None:
        """处理应用关闭事件"""
        self._running = False

        # 关闭所有 PeerConnections（并捕获异常）
        coros = []
        for pc in list(self._pcs):
            try:
                coros.append(pc.close())
            except Exception as e:
                logger.info("Error in _on_shutdown: %s", e)
        if coros:
            try:
                await asyncio.gather(*coros, return_exceptions=True)
            except Exception as e:
                logger.info("Error in _on_shutdown: %s", e)

        self._pcs.clear()

        # If a shared webcam was opened, stop it.
        if self._video_player is not None:
            try:
                if self._video_player.video is not None:
                    self._video_player.video.stop()
            except Exception as e:
                logger.info("Error in _on_shutdown: %s", e)

            self._video_player = None

        if self._audio_player is not None:
            try:
                if self._audio_player.audio is not None:
                    self._audio_player.audio.stop()
            except Exception as e:
                logger.info("Error in _on_shutdown: %s", e)
            self._audio_player = None

        if self._audio_capture is not None:
            try:
                self._audio_capture.stop()
            except Exception as e:
                logger.info("Error in _on_shutdown: %s", e)

    def _create_local_tracks(self, play_from, decode):
        """创建本地轨道"""

        if play_from:
            player = MediaPlayer(play_from, decode=decode)
            return player.audio, player.video
        else:
            options = {"framerate": "30", "video_size": "640x480"}
            if self._relay is None:
                if platform.system() == "Darwin":
                    self._video_player = MediaPlayer(
                        "default:none", format="avfoundation", options=options
                    )
                    self._audio_player = MediaPlayer("default", format="pulse")
                elif platform.system() == "Windows":
                    # 注意：设备名称在不同机器上不同，可能需要调整
                    # self._video_player = MediaPlayer(
                    #     "video=Integrated Camera", format="dshow", options=options
                    # )
                    options = self._config["webcam"]["video"]["options"]
                    input_format = options.pop("input_format", None)
                    self._video_player = MediaPlayer(
                        "video=3D Camera", format="dshow", options=options
                    )
                    # if input_format:
                    #     self._video_player.video.format = input_format
                    # ffmpeg -list_devices true -f dshow -i dummy
                    self._audio_player = MediaPlayer(
                        "audio=麦克风 (Realtek(R) Audio)", format="dshow")
                else:
                    # Linux
                    options = self._config["webcam"]["video"]["options"]
                    # # 添加高码率参数
                    # options.update({
                    #     "video_size": "3840x1080",     # 固定分辨率
                    #     "framerate": "30",              # 固定帧率
                    #     "b:v": "20M",                   # 固定平均码率
                    #     "maxrate": "20M",               # 限制最高码率
                    #     "bufsize": "40M",               # 缓冲区大小
                    #     "g": "60",                      # 关键帧间隔（帧数，30fps -> 2秒）
                    #     "preset": "veryfast",           # 编码速度优化
                    #     "qmin": "10",                   # 最小质量值（防止太糊）
                    #     "qmax": "42"                    # 最大质量值
                    # })

                    input_format = options.pop("input_format", None)
                    try:
                        self._video_player = MediaPlayer(
                            self._config["webcam"]["video"]["device"], format="v4l2", options=options)
                        if input_format:
                            self._video_player.video.format = input_format
                    except ValueError as e:
                        logger.info("Error opening video device: %s", e)
                        self._video_player = None
                    # Try pulse first, then alsa, then no audio
                    try:
                        self._audio_player = MediaPlayer(
                            self._config["webcam"]["audio"]["device"], format="pulse")
                    except ValueError:
                        try:
                            self._audio_player = MediaPlayer(
                                self._config["webcam"]["audio"]["device"], format="alsa")
                        except ValueError:
                            self._audio_player = None
                            if self._audio_capture is not None:
                                self._audio_capture.start()

                self._relay = MediaRelay()

            # 确保返回音频和视频轨道
            if self._audio_player is not None and self._audio_player.audio is not None:
                audio_track = self._relay.subscribe(self._audio_player.audio)
            else:
                audio_track = None
                if self._audio_capture is not None:
                    audio_track = self._relay.subscribe(self._audio_capture)

            video_track = None
            if self._video_player is not None and self._video_player.video is not None:
                flip_x = self._config["webcam"]["video"].get("flip_x", False)
                flip_y = self._config["webcam"]["video"].get("flip_y", False)

                if flip_x or flip_y:
                    video_track = VideoFlipTrack(
                        self._relay.subscribe(self._video_player.video), flip_x, flip_y)
                else:
                    video_track = self._relay.subscribe(
                        self._video_player.video)
            else:
                video_track = None

            # self._print_camera_info()
            return audio_track, video_track

    # def _print_camera_info(self):
    #     try:
    #         device = self._config["webcam"]["video"]["device"]
    #         result = subprocess.run(
    #             ["v4l2-ctl", "--device", device, "--all"],
    #             capture_output=True, text=True
    #         )
    #         if result.returncode == 0:
    #             for line in result.stdout.splitlines():
    #                 if "Width/Height" in line:
    #                     logger.info(f"[Camera] Resolution: {line.split(':')[-1].strip()}")
    #                 elif "Pixel Format" in line:
    #                     logger.info(f"[Camera] Format: {line.split(':')[-1].strip()}")
    #                 elif "Frames per second" in line:
    #                     logger.info(f"[Camera] FPS: {line.split(':')[-1].strip()}")
    #         else:
    #             logger.warning("[Camera] Could not query device with v4l2-ctl")
    #     except Exception as e:
    #         logger.warning(f"[Camera] Error querying camera: {e}")


    def _force_codec(self, pc, sender, forced_codec):
        """强制设置编解码器"""
        kind = forced_codec.split("/")[0]
        codecs = RTCRtpSender.getCapabilities(kind).codecs
        transceiver = next((t for t in pc.getTransceivers()
                           if t.sender == sender), None)
        if transceiver is None:
            return
        try:
            preferred = [
                codec for codec in codecs if codec.mimeType == forced_codec]
            for codec in preferred:
                if codec.mimeType.lower() == "video/h264":
                    codec.sdpFmtpLine = (
                        "profile-level-id=640c33;level-asymmetry-allowed=1;"
                        "packetization-mode=1;max-mbps=108000;max-fs=3600"
                    )
            if preferred:
                transceiver.setCodecPreferences(preferred)
        except Exception as e:
            logger.info("Error in _force_codec: %s", e)
