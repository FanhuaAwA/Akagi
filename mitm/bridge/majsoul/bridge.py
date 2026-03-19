from typing import Self, Iterable
from enum import Enum
from functools import cmp_to_key
import time
from .liqi import LiqiProto, MsgType
from ..bridge_base import BridgeBase
from ..logger import logger
        
MS_TILE_2_MJAI_TILE = {
    '0m': '5mr',
    '1m': '1m',
    '2m': '2m',
    '3m': '3m',
    '4m': '4m',
    '5m': '5m',
    '6m': '6m',
    '7m': '7m',
    '8m': '8m',
    '9m': '9m',
    '0p': '5pr',
    '1p': '1p',
    '2p': '2p',
    '3p': '3p',
    '4p': '4p',
    '5p': '5p',
    '6p': '6p',
    '7p': '7p',
    '8p': '8p',
    '9p': '9p',
    '0s': '5sr',
    '1s': '1s',
    '2s': '2s',
    '3s': '3s',
    '4s': '4s',
    '5s': '5s',
    '6s': '6s',
    '7s': '7s',
    '8s': '8s',
    '9s': '9s',
    '1z': 'E',
    '2z': 'S',
    '3z': 'W',
    '4z': 'N',
    '5z': 'P',
    '6z': 'F',
    '7z': 'C'
}
MJAI_TILE_2_MS_TILE = {
    '5mr': '0m',
    '1m': '1m',
    '2m': '2m',
    '3m': '3m',
    '4m': '4m',
    '5m': '5m',
    '6m': '6m',
    '7m': '7m',
    '8m': '8m',
    '9m': '9m',
    '5pr': '0p',
    '1p': '1p',
    '2p': '2p',
    '3p': '3p',
    '4p': '4p',
    '5p': '5p',
    '6p': '6p',
    '7p': '7p',
    '8p': '8p',
    '9p': '9p',
    '5sr': '0s',
    '1s': '1s',
    '2s': '2s',
    '3s': '3s',
    '4s': '4s',
    '5s': '5s',
    '6s': '6s',
    '7s': '7s',
    '8s': '8s',
    '9s': '9s',
    'E': '1z',
    'S': '2z',
    'W': '3z',
    'N': '4z',
    'P': '5z',
    'F': '6z',
    'C': '7z'
}


class Operation:
    NoEffect = 0
    Discard = 1
    Chi = 2
    Peng = 3
    AnGang = 4
    MingGang = 5
    JiaGang = 6
    Liqi = 7
    Zimo = 8
    Hu = 9
    LiuJu = 10
    BaBei = 11

class ActionChiPengGangType:
    Chi = 0
    Peng = 1
    Gang = 2

class OperationAnGangAddGang:
    AnGang = 3
    AddGang = 2

class MajsoulBridge(BridgeBase):
    def __init__(self):
        super().__init__()
        self.liqi_proto = LiqiProto()

        self.accountId = 0
        self.seat = 0
        self.lastDiscard = None
        self.reach = False
        self.accept_reach = None
        self.operation = {}
        self.AllReady = False
        self.latest_self_operation_list: list[dict] = []
        self.latest_self_operation_timing: dict = {}
        self.latest_action_step: int = -1
        self.temp = {}
        self.doras = []
        self.my_tehais = ["?"]*13
        self.my_tsumohai = "?"
        self.syncing = False

        self.mode_id = -1
        self.rank = -1
        self.score = -1

        self.is_3p = False
        self.inject_msg_id = 60000

    def reset(self):
        super().__init__()

        self.accountId = 0
        self.seat = 0
        self.lastDiscard = None
        self.reach = False
        self.accept_reach = None
        self.operation = {}
        self.AllReady = False
        self.latest_self_operation_list = []
        self.latest_self_operation_timing = {}
        self.latest_action_step = -1
        self.temp = {}
        self.doras = []
        self.my_tehais = ["?"]*13
        self.my_tsumohai = "?"
        self.syncing = False

        self.mode_id = -1
        self.rank = -1
        self.score = -1

        self.is_3p = False
        self.inject_msg_id = 60000


    def parse(self, content: bytes) -> None | list[dict]:
        """Parses the content and returns MJAI command.

        Args:
            content (bytes): Content to be parsed.

        Returns:
            None | list[dict]: MJAI command.
        """
        liqi_message = self.liqi_proto.parse(content)
        logger.debug(f"{liqi_message}")
        ret = self.parse_liqi(liqi_message)
        logger.debug(f"-> {ret}")
        return ret

    def parse_liqi(self, liqi_message: dict) -> None | list[dict]:
        ret = []

        if liqi_message is None:
            return None
        # Sync Game
        if ((liqi_message['method'] == '.lq.FastTest.syncGame' or liqi_message['method'] == '.lq.FastTest.enterGame')
            and liqi_message['type'] == MsgType.Res):
            self.syncing = True
            syncGame_msgs = LiqiProto().parse_syncGame(liqi_message)
            parsed_list = []
            parsed = []
            for msg in syncGame_msgs:
                parsed = self.parse_liqi(msg)
                if parsed:
                    parsed_list.extend(parsed)
            self.syncing = False
            if len(parsed_list)>=1:
                return parsed_list
            else:
                ret = []
                return ret
            
        # ready
        if liqi_message['method'] == '.lq.FastTest.fetchGamePlayerState' and liqi_message['type'] == MsgType.Res:
            # if liqi_message['data']['stateList'] == ['READY', 'READY', 'READY', 'READY']:
            self.AllReady = True
            return ret
        # start_game
        if liqi_message['method'] == '.lq.FastTest.authGame' and liqi_message['type'] == MsgType.Req:
            self.reset()
            self.accountId = liqi_message['data']['accountId']
            return ret
        if liqi_message['method'] == '.lq.FastTest.authGame' and liqi_message['type'] == MsgType.Res:
            self.is_3p = len(liqi_message['data']['seatList']) == 3
            try:
                self.mode_id = liqi_message['data']['gameConfig']['meta']['modeId']
            except:
                self.mode_id = -1

            seatList = liqi_message['data']['seatList']
            self.seat = seatList.index(self.accountId)
            ret.append({
                'type': 'start_game',
                'id': self.seat
            })
            return ret
        if liqi_message['method'] == '.lq.ActionPrototype':
            self.latest_action_step = int(liqi_message['data'].get('step', -1))
            self._capture_self_operation_list(liqi_message)
            # start_kyoku
            if liqi_message['data']['name'] == 'ActionNewRound':
                self.AllReady = False
                bakaze = ['E', 'S', 'W', 'N'][liqi_message['data']['data']['chang']]
                dora_marker = MS_TILE_2_MJAI_TILE[liqi_message['data']['data']['doras'][0]]
                self.doras = [dora_marker]
                honba = liqi_message['data']['data']['ben']
                oya = liqi_message['data']['data']['ju']
                kyoku = oya + 1
                kyotaku = liqi_message['data']['data']['liqibang']
                scores = liqi_message['data']['data']['scores']
                if self.is_3p:
                    scores = scores + [0]
                tehais = [['?']*13]*4
                my_tehais = ['?']*13
                for hai in range(13):
                    my_tehais[hai] = MS_TILE_2_MJAI_TILE[liqi_message['data']['data']['tiles'][hai]]
                if   len(liqi_message['data']['data']['tiles']) == 13:
                    tehais[self.seat] = sorted(my_tehais, key=cmp_to_key(compare_pai))
                    ret.append(
                        {
                            'type': 'start_kyoku',
                            'bakaze': bakaze,
                            'dora_marker': dora_marker,
                            'honba': honba,
                            'kyoku': kyoku,
                            'kyotaku': kyotaku,
                            'oya': oya,
                            'scores': scores,
                            'tehais': tehais
                        }
                    )
                elif len(liqi_message['data']['data']['tiles']) == 14:
                    self.my_tsumohai = MS_TILE_2_MJAI_TILE[liqi_message['data']['data']['tiles'][13]]
                    all_tehais = my_tehais + [self.my_tsumohai]
                    all_tehais = sorted(all_tehais, key=cmp_to_key(compare_pai))
                    tehais[self.seat] = all_tehais[:13]
                    ret.append(
                        {
                            'type': 'start_kyoku',
                            'bakaze': bakaze,
                            'dora_marker': dora_marker,
                            'honba': honba,
                            'kyoku': kyoku,
                            'kyotaku': kyotaku,
                            'oya': oya,
                            'scores': scores,
                            'tehais': tehais
                        }
                    )
                    ret.append(
                        {
                            'type': 'tsumo',
                            'actor': self.seat,
                            'pai': all_tehais[13]
                        }
                    )
                else:
                    raise

            if self.accept_reach is not None:
                ret.append(self.accept_reach)
                self.accept_reach = None

            # According to mjai.app, in the case of an ankan, the dora event comes first, followed by the tsumo event.
            if 'data' in liqi_message['data']:
                if 'doras' in liqi_message['data']['data']:
                    if len(liqi_message['data']['data']['doras']) > len(self.doras):
                        ret.append(
                            {
                                'type': 'dora',
                                'dora_marker': MS_TILE_2_MJAI_TILE[liqi_message['data']['data']['doras'][-1]]
                            }
                        )
                        self.doras = liqi_message['data']['data']['doras']
                
            # tsumo
            if liqi_message['data']['name'] == 'ActionDealTile':
                actor = liqi_message['data']['data']['seat']
                if liqi_message['data']['data']['tile'] == '':
                    pai = '?'
                else:
                    pai = MS_TILE_2_MJAI_TILE[liqi_message['data']['data']['tile']]
                    self.my_tsumohai = pai
                ret.append(
                    {
                        'type': 'tsumo',
                        'actor': actor,
                        'pai': pai
                    }
                )
            # dahai
            if liqi_message['data']['name'] == 'ActionDiscardTile':
                actor = liqi_message['data']['data']['seat']
                self.lastDiscard = actor
                pai = MS_TILE_2_MJAI_TILE[liqi_message['data']['data']['tile']]
                tsumogiri = liqi_message['data']['data']['moqie']
                if liqi_message['data']['data']['isLiqi']:
                    ret.append(
                        {
                            'type': 'reach',
                            'actor': actor
                        }
                    )
                ret.append(
                    {
                        'type': 'dahai',
                        'actor': actor,
                        'pai': pai,
                        'tsumogiri': tsumogiri
                    }
                )
                if liqi_message['data']['data']['isLiqi']:
                    self.accept_reach = {
                                            'type': 'reach_accepted',
                                            'actor': actor
                                        }
            # Reach
            if liqi_message['data']['name'] == 'ActionReach':
                # TODO
                pass
            # ChiPonKan
            if liqi_message['data']['name'] == 'ActionChiPengGang':
                actor = liqi_message['data']['data']['seat']
                target = actor
                consumed = []
                pai = ''
                for idx, seat in enumerate(liqi_message['data']['data']['froms']):
                    if seat != actor:
                        target = seat
                        pai = MS_TILE_2_MJAI_TILE[liqi_message['data']['data']['tiles'][idx]]
                    else:
                        consumed.append(MS_TILE_2_MJAI_TILE[liqi_message['data']['data']['tiles'][idx]])
                assert target != actor
                assert len(consumed) != 0
                assert pai != ''
                match liqi_message['data']['data']['type']:
                    case ActionChiPengGangType.Chi:
                        assert len(consumed) == 2
                        ret.append(
                            {
                                'type': 'chi',
                                'actor': actor,
                                'target': target,
                                'pai': pai,
                                'consumed': consumed
                            }
                        )
                        pass
                    case ActionChiPengGangType.Peng:
                        assert len(consumed) == 2
                        ret.append(
                            {
                                'type': 'pon',
                                'actor': actor,
                                'target': target,
                                'pai': pai,
                                'consumed': consumed
                            }
                        )
                    case ActionChiPengGangType.Gang:
                        assert len(consumed) == 3
                        ret.append(
                            {
                                'type': 'daiminkan',
                                'actor': actor,
                                'target': target,
                                'pai': pai,
                                'consumed': consumed
                            }
                        )
                        pass
                    case _:
                        raise
            # AnkanKakan
            if liqi_message['data']['name'] == 'ActionAnGangAddGang':
                actor = liqi_message['data']['data']['seat']
                match liqi_message['data']['data']['type']:
                    case OperationAnGangAddGang.AnGang:
                        pai = MS_TILE_2_MJAI_TILE[liqi_message['data']['data']['tiles']]
                        consumed = [pai.replace("r", "")]*4
                        if pai[0] == '5' and pai[1] != 'z':
                            consumed[0] += 'r'
                        ret.append(
                            {
                                'type': 'ankan',
                                'actor': actor,
                                'consumed': consumed
                            }
                        )
                    case OperationAnGangAddGang.AddGang:
                        pai = MS_TILE_2_MJAI_TILE[liqi_message['data']['data']['tiles']]
                        consumed = [pai.replace("r", "")] * 3
                        if pai[0] == "5" and not pai.endswith("r"):
                            consumed[0] = consumed[0] + "r"
                        ret.append(
                            {
                                'type': 'kakan',
                                'actor': actor,
                                'pai': pai,
                                'consumed': consumed
                            }
                        )

            if liqi_message['data']['name'] == 'ActionBaBei':
                actor = liqi_message['data']['data']['seat']
                ret.append(
                    {
                        'type': 'nukidora',
                        'actor': actor,
                        'pai': 'N'
                    }
                )

            # hora
            if liqi_message['data']['name'] == 'ActionHule':
                ret = []
                ret.extend(self._build_hora_events(liqi_message['data']['data']))
                ret.append({'type': 'end_kyoku'})
                return ret
            # notile
            if liqi_message['data']['name'] == 'ActionNoTile':
                ret = []
                ret.append({'type': 'ryukyoku'})
                ret.append({'type': 'end_kyoku'})
                return ret
            # ryukyoku
            if liqi_message['data']['name'] == 'ActionLiuJu':
                ret = []
                ret.append({'type': 'ryukyoku'})
                ret.append({'type': 'end_kyoku'})
                return ret
            if 'data' in liqi_message['data']:
                if 'operation' in liqi_message['data']['data']:
                    return ret
        # end_game
        if liqi_message['method'] == '.lq.NotifyGameEndResult' or liqi_message['method'] == '.lq.NotifyGameTerminate':
            try:
                for idx, player in enumerate(liqi_message['data']['result']['players']):
                    if player['seat'] == self.seat:
                        self.rank = idx + 1
                        self.score = player['partPoint1']
            except:
                pass
            ret.append(
                {
                    'type': 'end_game'
                }
            )
            return ret
        return ret

    def _capture_self_operation_list(self, liqi_message: dict) -> None:
        payload = liqi_message.get('data', {}).get('data', {})
        operation = payload.get('operation')
        if not operation:
            actor_seat = payload.get('seat')
            if actor_seat is not None and int(actor_seat) == self.seat:
                self.latest_self_operation_list = []
                self.latest_self_operation_timing = {}
            return

        seat = int(operation.get('seat', -1))
        if seat != self.seat:
            return

        self.latest_self_operation_timing = {
            "captured_at": time.monotonic(),
            "time_fixed_ms": int(operation.get("timeFixed", operation.get("time_fixed", 0)) or 0),
            "time_add_ms": int(operation.get("timeAdd", operation.get("time_add", 0)) or 0),
        }

        operation_list = operation.get('operationList', operation.get('operation_list', []))
        normalized_ops: list[dict] = []
        for op_index, item in enumerate(operation_list):
            normalized_ops.append(
                {
                    'type': int(item.get('type', 0)),
                    'op_index': int(op_index),
                    'combination': list(item.get('combination', [])),
                    'change_tiles': list(item.get('changeTiles', item.get('change_tiles', []))),
                    'tile_states': [
                        int(state)
                        for state in item.get('changeTileStates', item.get('change_tile_states', []))
                    ],
                    'gap_type': int(item.get('gapType', item.get('gap_type', 0)) or 0),
                }
            )
        self.latest_self_operation_list = normalized_ops

    def _build_hora_events(self, payload: dict) -> list[dict]:
        hules = list(payload.get('hules', []))
        if not hules:
            return []

        events: list[dict] = []
        for hule in hules:
            actor = int(hule.get('seat', -1))
            if actor < 0:
                continue
            if bool(hule.get('zimo')):
                target = actor
            elif self.lastDiscard is not None:
                target = int(self.lastDiscard)
            else:
                target = actor
            hu_tile = hule.get('huTile', '')
            pai = MS_TILE_2_MJAI_TILE.get(hu_tile, hu_tile)
            if not pai:
                pai = '?'
            events.append(
                {
                    'type': 'hora',
                    'actor': actor,
                    'target': target,
                    'pai': pai,
                }
            )
        return events

    def build(self, command: dict) -> None | bytes:
        if not isinstance(command, dict):
            return None

        action_type = command.get("type")
        if action_type is None:
            return None

        if action_type == "none":
            return self._build_skip_request()

        if action_type in {"chi", "pon", "daiminkan"}:
            return self._build_chi_peng_gang_request(action_type, command)

        return self._build_self_operation_request(action_type, command)

    def _build_skip_request(self) -> None | bytes:
        if not self.latest_self_operation_list:
            return None

        cpg_ops = [op for op in self.latest_self_operation_list if op.get("type") in {2, 3, 5}]
        if cpg_ops:
            cpg_op = cpg_ops[0]
            data = {
                "type": int(cpg_op.get("type", 0)),
                "index": 0,
                "cancelOperation": True,
                "timeuse": 1,
            }
            return self._compose_request(".lq.FastTest.inputChiPengGang", data)

        fallback_type = int(self.latest_self_operation_list[0]["type"]) if self.latest_self_operation_list else 0
        data = {
            "type": fallback_type,
            "index": 0,
            "cancelOperation": True,
            "timeuse": 1,
        }
        return self._compose_request(".lq.FastTest.inputOperation", data)

    def _build_chi_peng_gang_request(self, action_type: str, command: dict) -> None | bytes:
        operation_map = {
            "chi": Operation.Chi,
            "pon": Operation.Peng,
            "daiminkan": Operation.MingGang,
        }
        operation_type = operation_map[action_type]
        operation = self._find_operation(operation_type)
        if operation is None:
            logger.warning(f"Skip {action_type} injection: operation type {operation_type} not available")
            return None
        index = self._find_operation_index(
            operation,
            consumed=command.get("consumed"),
            tile=command.get("pai"),
        )
        data = {
            # ReqChiPengGang uses the operation-list type values (2/3/5),
            # while ActionChiPengGang notify uses a separate 0/1/2 enum.
            "type": int(operation_type),
            "index": index,
            "cancelOperation": False,
            "timeuse": 1,
        }
        return self._compose_request(".lq.FastTest.inputChiPengGang", data)

    def _build_self_operation_request(self, action_type: str, command: dict) -> None | bytes:
        if action_type == "nukidora":
            operation = self._find_operation(Operation.BaBei)
            if operation is None:
                logger.warning("Skip nukidora injection: BaBei operation not available")
                return None
            mode = str(command.get("_nukidora_mode") or "operation")
            if mode != "discard":
                data = {
                    "type": int(Operation.BaBei),
                    "index": int(operation.get("op_index", 0)),
                    "cancelOperation": False,
                    "timeuse": 1,
                }
                return self._compose_request(".lq.FastTest.inputOperation", data)
            fallback_tile = str(command.get("pai") or "N")
            fallback_command = {
                "type": "dahai",
                "pai": fallback_tile,
                "tsumogiri": bool(self.my_tsumohai == fallback_tile),
                "_force_discard": True,
            }
            return self._build_self_operation_request("dahai", fallback_command)

        if action_type == "dahai" and command.get("pai") == "N" and not bool(command.get("_force_discard")):
            discard_operation = self._find_operation(Operation.Discard)
            babei_operation = self._find_operation(Operation.BaBei)
            if discard_operation is None and babei_operation is not None:
                logger.debug(
                    "Treating dahai N as nukidora because discard operation is unavailable "
                    f"while BaBei remains available at step {self.latest_action_step}"
                )
                fallback_command = dict(command)
                fallback_command["type"] = "nukidora"
                fallback_command.setdefault("_nukidora_mode", "operation")
                return self._build_self_operation_request("nukidora", fallback_command)

        operation_type = self._operation_type_for_action(action_type)
        if operation_type is None:
            logger.debug(f"Unsupported majsoul action for injection: {action_type}")
            return None

        operation = self._find_operation(operation_type)
        if operation is None:
            logger.warning(f"Skip {action_type} injection: operation type {operation_type} not available")
            return None
        tile = command.get("pai")
        if action_type in {"dahai", "reach"} and not tile:
            logger.warning(f"Skip {action_type} injection because tile is missing: {command}")
            return None
        tile_ms = self._to_ms_tile(tile) if tile else ""

        index = self._find_operation_index(
            operation,
            consumed=command.get("consumed"),
            tile=tile,
        )
        tile_state = None
        if tile and operation:
            change_tiles = list(operation.get("change_tiles", []))
            tile_states = list(operation.get("tile_states", []))
            for i, change_tile in enumerate(change_tiles):
                change_tile_mjai = MS_TILE_2_MJAI_TILE.get(change_tile, change_tile)
                if not self._tile_matches(tile, change_tile_mjai):
                    continue
                tile_ms = change_tile
                index = i
                if i < len(tile_states):
                    tile_state = int(tile_states[i])
                break

        data = {
            "type": int(operation_type),
            "index": int(index),
            "cancelOperation": False,
            "timeuse": 1,
        }
        if tile_ms:
            data["tile"] = tile_ms
        if action_type in {"dahai", "reach"}:
            data["moqie"] = bool(command.get("tsumogiri"))
        if tile_state is not None:
            data["tileState"] = int(tile_state)
        if operation and int(operation.get("gap_type", 0) or 0) != 0:
            data["gapType"] = int(operation["gap_type"])
        return self._compose_request(".lq.FastTest.inputOperation", data)

    def _compose_request(self, method: str, data: dict) -> None | bytes:
        req = {
            "type": MsgType.Req,
            "method": method,
            "data": data,
        }
        req_id = self._next_inject_msg_id()
        try:
            return self.liqi_proto.compose(req, msg_id=req_id)
        except Exception as exc:
            logger.error(f"Failed to compose {method} with data={data}: {exc}")
            return None

    def _next_inject_msg_id(self) -> int:
        for _ in range(4096):
            candidate = self.inject_msg_id
            self.inject_msg_id += 1
            if self.inject_msg_id >= 65535:
                self.inject_msg_id = 60000
            if candidate not in self.liqi_proto.res_type:
                return candidate
        # fallback: avoid blocking autoplay if id allocation is exhausted
        return (int(self.liqi_proto.msg_id) + 1) % 65535 or 1

    def _operation_type_for_action(self, action_type: str) -> int | None:
        match action_type:
            case "dahai":
                return Operation.Discard
            case "ankan":
                return Operation.AnGang
            case "kakan":
                return Operation.JiaGang
            case "reach":
                return Operation.Liqi
            case "hora":
                return self._resolve_hora_operation_type()
            case "ryukyoku":
                return Operation.LiuJu
            case "nukidora":
                return Operation.BaBei
            case _:
                return None

    def _resolve_hora_operation_type(self) -> int:
        types = [int(op.get("type", 0)) for op in self.latest_self_operation_list]
        if Operation.Zimo in types:
            return Operation.Zimo
        if Operation.Hu in types:
            return Operation.Hu
        return Operation.Hu

    def _find_operation(self, operation_type: int) -> dict | None:
        for operation in self.latest_self_operation_list:
            if int(operation.get("type", 0)) == int(operation_type):
                return operation
        return None

    def _find_operation_index(
        self,
        operation: dict | None,
        consumed: list[str] | None = None,
        tile: str | None = None,
    ) -> int:
        if not operation:
            return 0

        combinations = list(operation.get("combination", []))
        if not combinations:
            return int(operation.get("op_index", 0))

        if consumed:
            target_tiles = [str(item) for item in consumed if isinstance(item, str) and item]
            target = self._safe_sort_tiles(target_tiles)
            for idx, combination in enumerate(combinations):
                normalized = self._safe_sort_tiles(self._normalize_combination(combination))
                if normalized == target:
                    return idx

        if tile:
            for idx, combination in enumerate(combinations):
                normalized = self._normalize_combination(combination)
                if any(self._tile_matches(tile, candidate_tile) for candidate_tile in normalized):
                    return idx

        return 0

    def _normalize_combination(self, combination: str | Iterable[str]) -> list[str]:
        if isinstance(combination, str):
            raw_tiles = [token for token in combination.split("|") if token]
        else:
            raw_tiles = [str(token) for token in combination if token]
        return [MS_TILE_2_MJAI_TILE.get(tile, tile) for tile in raw_tiles]

    def _to_ms_tile(self, tile: str) -> str:
        return MJAI_TILE_2_MS_TILE.get(tile, tile)

    def _safe_sort_tiles(self, tiles: list[str]) -> list[str]:
        try:
            return sorted(tiles, key=cmp_to_key(compare_pai))
        except Exception:
            return sorted(tiles)

    def _tile_matches(self, target: str, current: str) -> bool:
        if target == current:
            return True
        if target.endswith("r"):
            return target[:2] == current[:2]
        if current.endswith("r"):
            return target[:2] == current[:2]
        return target == current

def compare_pai(pai1: str, pai2: str):
    # Smallest
    # 1m~4m, 5mr, 5m~9m,
    # 1p~4p, 5pr, 5p~9p,
    # 1s~4s, 5sr, 5s~9s,
    # E, S, W, N, P, F, C, ?
    # Biggest
    pai_order = [
        '1m', '2m', '3m', '4m', '5mr', '5m', '6m', '7m', '8m', '9m',
        '1p', '2p', '3p', '4p', '5pr', '5p', '6p', '7p', '8p', '9p',
        '1s', '2s', '3s', '4s', '5sr', '5s', '6s', '7s', '8s', '9s',
        'E', 'S', 'W', 'N', 'P', 'F', 'C', '?'
    ]
    idx1 = pai_order.index(pai1)
    idx2 = pai_order.index(pai2)
    if idx1 > idx2:
        return 1  
    elif idx1 == idx2:
        return 0
    else:
        return -1
