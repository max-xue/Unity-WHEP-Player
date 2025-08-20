
import asyncio
import cv2
from av import VideoFrame
from aiortc import VideoStreamTrack
from aiortc.contrib.media import MediaStreamError

from gros.fupl.config.logger import get_logger

# 获取日志记录器
logger = get_logger(__name__)

class VideoFlipTrack(VideoStreamTrack):
    """
    视频轨道，用于翻转视频帧（更稳健的队列和异常处理）
    """

    def __init__(self, track, flip_x, flip_y):
        """
        初始化视频轨道，用于翻转视频帧
        :param track: 视频轨道
        :param flipX: 是否水平翻转
        :param flipY: 是否垂直翻转
        """
        super().__init__()
        self.track = track
        self._queue = asyncio.Queue(maxsize=5)  # 限制队列大小
        self._running = True
        self._flip_x = flip_x
        self._flip_y = flip_y
        self._task = asyncio.create_task(self._process_frames())

    async def _process_frames(self):
        try:
            while self._running:
                try:
                    frame = await self.track.recv()
                except Exception as e:
                    logger.info("Error in _process_frames: %s", e)

                    # 原始轨道关闭或出错，退出循环
                    break

                if not self._running:
                    break

                # 如果队列已满，丢弃最旧的帧
                if self._queue.full():
                    try:
                        self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass

                try:
                    await self._queue.put(frame)
                except asyncio.CancelledError:
                    break
        finally:
            # 清理队列
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except Exception as e:
                    logger.info("Error in _process_frames: %s", e)

                    break
            # 释放资源
            try:
                if self.track is not None and hasattr(self.track, 'stop'):
                    self.track.stop()
            except Exception as e:
                logger.info("Error in _process_frames: %s", e)

                pass
            self._running = False

    async def recv(self):
        if not self._running:
            raise MediaStreamError
        try:
            frame = await self._queue.get()
            
            # if not hasattr(self, "_info_logged"):
            #     logger.info(f"[Camera] Resolution: {frame.width}x{frame.height}")
            #     logger.info(f"[Camera] FPS: {getattr(frame, 'rate', 'unknown')}")
            #     logger.info(f"[Camera] Format: {frame.format.name if frame.format else 'unknown'}")
            #     self._info_logged = True
        except Exception:
            raise MediaStreamError

        img = frame.to_ndarray(format="bgr24")
        if self._flip_x and self._flip_y:
            img = cv2.flip(img, -1)  # -1 表示同时翻转X和Y
        elif self._flip_x:
            img = cv2.flip(img, 1)  # 1 表示水平翻转
        elif self._flip_y:
            img = cv2.flip(img, 0)  # 0 表示垂直翻转

        new_frame = VideoFrame.from_ndarray(img, format="bgr24")
        try:
            new_frame.pts = frame.pts
            new_frame.time_base = frame.time_base
        except Exception as e:
            logger.info("Error in recv: %s", e)

            pass
        return new_frame

    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            try:
                self._task.cancel()
            except Exception as e:
                logger.info("Error in stop: %s", e)

                pass
        try:
            if hasattr(self.track, 'stop'):
                self.track.stop()
        except Exception as e:
            logger.info("Error in stop: %s", e)

            pass

