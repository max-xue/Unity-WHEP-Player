'''音频捕获类'''
import asyncio
from fractions import Fraction
import numpy as np
import sounddevice as sd
from aiortc import MediaStreamTrack
from av import AudioFrame

from gros.fupl.config.logger import get_logger

# 获取日志记录器
logger = get_logger(__name__)

class AudioStream(MediaStreamTrack):
    """音频捕获类"""

    kind = "audio"

    def __init__(self, config):
        super().__init__()
        self._config = config
        self._queue = asyncio.Queue()
        self.samples_sent = 0
        self._stream = None

        # ----------- 自动选择输入设备 -----------
        try:
            sd.check_input_settings(samplerate=self._config["sample_rate"], channels=self._config["channels"])
            device = None  # 使用默认设备
        except Exception:
            # 默认设备不可用 → 查找可用设备
            devices = sd.query_devices()
            device = None
            for idx, d in enumerate(devices):
                if d["max_input_channels"] >= self._config["channels"]:
                    try:
                        sd.check_input_settings(
                            device=idx,
                            samplerate=self._config["sample_rate"],
                            channels=self._config["channels"]
                        )
                        device = idx
                        break
                    except Exception:
                        continue
            if device is None:
                logger.error(
                    f"No usable audio input device found for "
                    f"samplerate={self._config['sample_rate']}, channels={self._config['channels']}"
                )
                return

        # 定义回调函数
        def callback(indata, frames, time, status):
            if status:
                logger.info("Mic status: %s", status)
            # 转成 int16 PCM
            pcm = (indata * 32767).astype(np.int16)
            self._queue.put_nowait(pcm)

        # 初始化音频流
        self._stream = sd.InputStream(
            device=device,  # 使用找到的有效设备
            samplerate=self._config["sample_rate"],
            channels=self._config["channels"],
            dtype='float32',
            # device=self._config["device_index"],
            blocksize=self._config["frame_size"],
            callback=callback,
            # latency="high"
        )
    
        logger.info("AudioStream init, sample_rate=%d, channels=%d, frame_size=%d, device=%s",
                    self._config["sample_rate"], self._config["channels"], self._config["frame_size"], str(device))


    def start(self):
        """启动音频流"""
        if self._stream is None:
            return
        
        self._stream.start()
        logger.info("AudioStream started")

    async def recv(self):
        """WebRTC获取音频帧"""
        pcm = await self._queue.get()
        frame = AudioFrame(format="s16", layout="mono", samples=len(pcm))
        frame.planes[0].update(pcm.tobytes())
        frame.sample_rate = self._config["sample_rate"]

        # 设置时间戳 (PTS)
        frame.pts = self.samples_sent
        frame.time_base = Fraction(1, self._config["sample_rate"])
        self.samples_sent += len(pcm)

        return frame

    def stop(self):
        """停止音频流"""
        if self._stream:
            self._stream.stop()
            self._stream.close()
