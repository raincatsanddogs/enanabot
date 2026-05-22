from pydantic import BaseModel


class Config(BaseModel):
    """Mineflayer WebSocket bridge configuration."""

    mineflayer_ws_host: str = "localhost"
    mineflayer_ws_port: int = 3001
    mineflayer_ws_token: str = "change-me"
    mineflayer_ws_account_preset: int = 1
    mineflayer_ws_server_preset: int = 1
    mineflayer_ws_request_timeout: int = 10
    mineflayer_ws_player_poll_interval: int = 300
    mineflayer_ws_forward_prefix: str = "[群聊]>>"
    mineflayer_ws_mc_prefix: str = "[插件服]>>"
    mineflayer_enable_mcgen: bool = True
    mineflayer_mcgen_api_url: str = "https://mcgen.menzerath.eu"
