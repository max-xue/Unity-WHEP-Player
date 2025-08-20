''' 扬声器播放 Track '''
import sounddevice as sd
import numpy as np
import threading
import queue
import time

from aiortc import MediaStreamTrack
from gros.fupl.config.logger import get_logger

# 获取日志记录器
logger = get_logger(__name__)

# ---------------- 扬声器播放 Track ----------------
class SpeakerPlayback:
    ''' 扬声器播放 Track '''
    def __init__(self, config):
        self._config = config
        self.sr = int(self._config["sample_rate"])
        self.channels = int(self._config["channels"])
        self.frame_size = int(self._config.get("frame_size", 960))  # fallback

        # ----------- 自动选择输出设备 -----------
        try:
            sd.check_output_settings(samplerate=self.sr, channels=self.channels)
            device = None  # 使用默认设备
        except Exception:
            # 默认设备不可用 → 找一个可用的
            devices = sd.query_devices()
            device = None
            for idx, d in enumerate(devices):
                if d["max_output_channels"] >= self.channels:
                    try:
                        sd.check_output_settings(
                            device=idx,
                            samplerate=self.sr,
                            channels=self.channels
                        )
                        device = idx
                        break
                    except Exception:
                        continue
            if device is None:
                logger.error(
                    f"No usable audio output device found for "
                    f"samplerate={self.sr}, channels={self.channels}"
                )
                return

        # ----------- 打开音频流 -----------
        self.stream = sd.OutputStream(
            samplerate=self.sr,
            channels=self.channels,
            dtype="float32",
            blocksize=self.frame_size,
            device=device
        )
        self.stream.start()

        self._q = queue.Queue(maxsize=200)
        self._running = True
        self._thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._thread.start()

        logger.info("SpeakerPlayback started, sample_rate=%d, channels=%d, frame_size=%d, device=%s",
                    self.sr, self.channels, self.frame_size, str(device))

    def enqueue_frame(self, arr: np.ndarray):
        """
        arr: numpy array with shape (samples, channels) and dtype float32 in [-1,1]
        """
        try:
            if arr.ndim == 1:
                arr = arr.reshape(-1, 1)

            if arr.shape[1] != self.channels:
                if arr.shape[1] == 1 and self.channels == 2:
                    arr = np.repeat(arr, 2, axis=1)
                else:
                    minc = min(arr.shape[1], self.channels)
                    out = np.zeros((arr.shape[0], self.channels), dtype=np.float32)
                    out[:, :minc] = arr[:, :minc]
                    arr = out

            try:
                self._q.put_nowait(arr.copy())
            except queue.Full:
                pass
        except Exception as e:
            logger.info("Error in enqueue_frame %s", e)

    def _playback_loop(self):
        while self._running:
            try:
                arr = self._q.get()
                if arr is None:
                    break
                if arr.dtype != np.float32:
                    arr = arr.astype(np.float32)
                self.stream.write(arr)
                
                # 打印当前队列长度
                # qsize = self._q.qsize()
                # print(f"[Playback] queue size={qsize}")
            except Exception as e:
                logger.info("Error in playback_loop %s", e)
                time.sleep(0.01)

    def stop(self):
        self._running = False
        try:
            self._q.put_nowait(None)
        except Exception as e:
            logger.info("Error in stop %s", e)
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception as e:
            logger.info("Error in stop %s", e)

class AudioReceiverTrack(MediaStreamTrack):
    """ 音频接收 Track """
    kind = "audio"

    def __init__(self, track, player: SpeakerPlayback):
        super().__init__()
        self.track = track
        self.player = player

    async def recv(self):
        frame = await self.track.recv()
        try:
            arr = frame.to_ndarray()  # shape may be (channels, samples), (1, N), etc.
        except Exception as e:
            logger.info("Error in recv %s", e)
            pcm = frame.planes[0].to_bytes()
            arr = np.frombuffer(pcm, dtype=np.int16)

        # print("AFRAME: format=%s, layout=%s, sample_rate=%s, arr_shape=%s, dtype=%s" %
        #       (getattr(frame, "format", None),
        #        getattr(frame, "layout", None),
        #        getattr(frame, "sample_rate", None),
        #        arr.shape, arr.dtype))

        # --- 修正 shape ---
        if arr.shape[0] == 1 and frame.layout.name == "stereo":
            # interleaved stereo: 单平面 (1, N) → (samples, 2)
            arr = arr.reshape(-1, 2)
        elif arr.ndim == 2 and arr.shape[0] == 2:
            # planar stereo: (2, samples) → (samples, 2)
            arr = arr.T

        # 转 float32 [-1, 1]
        if arr.dtype == np.int16:
            arr = arr.astype(np.float32) / 32767.0
        else:
            arr = arr.astype(np.float32)

        # 放进播放队列（不会阻塞 recv）
        self.player.enqueue_frame(arr)
        return frame
