# about the project

此项目以JavaScript编写，基于mineflayer，可以做到：读取账号信息，进行登录，发送服务器消息至console以供解析

## configs/

解析配置项

## login/

用于服务器登录

## handler/

用于各类消息解析

## 食用方法

node index.js

参数：-p 1 以第一个档案启动
-s 1 以第一个服务器启动
在不加参数时默认以第一个档案第一个服务器启动

## console 消息输出参考格式

```json
{
  "timestamp":"1145141919810",//时间戳，为自 1970-1-1 00:00:00 UTC（世界标准时间）至今所经过的毫秒数。
  "msg":{
    "timestamp":"2011-10-05T14:48:00.000Z",//也是时间戳，但是为收到消息的时间
    "type":"join",//type类型为：join, left, whisper, kill, chat, server_chat, server_cmd
    "translate":"multiplayer.player.join",//mc的键名，非原版消息可能会没有此项
    "text":"awa",//基本和translate项互斥，即非原版消息/聊天会有此项
    "params":[
      {
        "type":"type",//类型，基本是实体的键名
        "name":"name",//若击杀者无名字则使用键名
        "uuid":"uuid"//实体uuid，但是放出来没什么意义？
      }//此数组的长度不定，最高为3，若为非原版消息/聊天则格式为：[{"":"消息"}]
    ]
  }
}
```

## console 消息输入参考格式

```json
{
  "timestamp":"time",
  "group":"",
  "sender":"",
  "msg":""
}
```

## todo

- v1
- [x] 多账号、多皮肤站登录
- [x] 多服务器登录
- [x] 消息处理
- [x] 连接nonebot，并配置权限组，指令
- v2
- [] 私聊消息处理
- [] 容器处理
- v3
- [] 瞄准
- [] 进食
- [] 攻击

