# -*- coding: utf-8 -*-
"""Webcam处理"""

from gros.fupl.handler.handler_base import HandlerBase
from gros.fupl.handler.webcam.webcam_server import WebcamServer
from gros.fupl.config.logger import get_logger

# 获取日志记录器
logger = get_logger(__name__)

class WebcamHandler(HandlerBase):
    """Webcam处理"""
    def __init__(self, handler_type, server):
        """初始化"""
        super().__init__(handler_type, server)

        self._webcam_server = WebcamServer(self.config)

    def start(self):
        """启动"""
        logger.info("WebcamHandler started")
        # 启动Webcam服务器
        self._webcam_server.run()

    def stop(self):
        """停止"""
        logger.info("WebcamHandler stopped")
        # 停止Webcam服务器
        self._webcam_server.stop()

    def update(self):
        """更新"""
        # logger.info("WebcamHandler updated")

    def disconnect(self):
        """断开连接"""
        logger.info("WebcamHandler disconnected")
