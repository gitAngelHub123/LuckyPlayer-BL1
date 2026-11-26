# Lucky Player - Borderlands GOTY Enhanced
# - Cada jefe suelta un arma legendaria (naranja) asegurada, además de su botín normal.
# - Cada cofre con armas suelta un arma legendaria encima.
# - Velocidad y salto multiplicados (ajustables).
# - Experiencia de misiones multiplicada (ajustable).
# MIT License (c) 2026 Angel - ver LICENSE
from pathlib import Path
from typing import Any

import unrealsdk
from mods_base import build_mod, hook, SliderOption
from unrealsdk.hooks import Block, Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

REGISTRO = Path(__file__).parent / "registro.txt"
RAREZA_NARANJA = 50
CALIDAD = 200
INTENTOS = 120
BOLSA_ARMAS = "gd_itempools.WeaponPools.Weapons_All"

op_vel = SliderOption("Multiplicador de velocidad", 2.0, 1.0, 3.0, 0.5, is_integer=False)
op_salto = SliderOption("Multiplicador de salto", 3.0, 1.0, 3.0, 0.5, is_integer=False)
op_xp = SliderOption("Experiencia recibida por misión", 5.0, 0.1, 5.0, 0.1, is_integer=False,
                     description="Multiplica la experiencia que da cada misión completada.")

op_xp_general = SliderOption("Multiplicador de experiencia en general", 1, 1, 10, 1,
                             description="Experiencia de todo lo que no sea misiones: matar, cofres, desafíos...")

_estado: dict[str, Any] = {"pawn": None, "vel": 0.0, "salto": 0.0, "mision": 0, "generando": False}
_cofres_hechos: set[str] = set()
_jefes_hechos: set[str] = set()


def log(msg: str, consola: bool = False) -> None:
    if consola or msg.startswith("ERROR"):
        print(f"[LuckyPlayer] {msg}")
    try:
        with REGISTRO.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- movimiento
def _aplicar_movimiento(pawn: UObject) -> None:
    base = pawn.Class.ClassDefaultObject
    pawn.GroundSpeed = base.GroundSpeed * op_vel.value
    pawn.JumpZ = base.JumpZ * op_salto.value
    _estado.update(pawn=pawn, vel=op_vel.value, salto=op_salto.value)


@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def on_tick(obj: UObject, _a: WrappedStruct, _r: Any, _f: BoundFunction) -> None:
    pawn = obj.Pawn
    if pawn is None:
        return
    try:
        base = pawn.Class.ClassDefaultObject
        if (pawn is not _estado["pawn"] or _estado["vel"] != op_vel.value
                or _estado["salto"] != op_salto.value
                or abs(pawn.GroundSpeed - base.GroundSpeed * op_vel.value) > 1):
            _aplicar_movimiento(pawn)
    except Exception as e:
        log(f"ERROR movimiento: {e!r}")


# ---------------------------------------------------------------- botín
def _nivel_jugador(defecto: int = 1) -> int:
    try:
        for pc in unrealsdk.find_all("WillowPlayerController", exact=False):
            if pc.Pawn is not None:
                return int(pc.Pawn.GetExpLevel())
    except Exception:
        pass
    return defecto


def _generar_una_vez(bolsa: UObject, contexto: UObject, nivel: int) -> list[UObject]:
    cdo = unrealsdk.find_class("ItemPool").ClassDefaultObject
    ret = cdo.SpawnBalancedInventoryFromPool(
        Definition=bolsa, GameStage=nivel, AwesomeLevel=CALIDAD,
        ContextSource=contexto, SpawnedInventory=[], OuterPoolChance=1.0)
    items: list[UObject] = []
    for v in (ret if isinstance(ret, tuple) else (ret,)):
        try:
            items += [x for x in v if isinstance(x, UObject)]
        except TypeError:
            pass
    return items


def _naranja_de(ruta: str, contexto: UObject, nivel: int) -> UObject | None:
    """Fabrica copias hasta que una llega a naranja; devuelve la mejor (o nada)."""
    try:
        bolsa = unrealsdk.find_object("ItemPoolDefinition", ruta)
    except Exception:
        bolsa = None
    if bolsa is None:
        log(f"ERROR: no encontré la bolsa {ruta}")
        return None
    mejor, mejor_r = None, -1
    _estado["generando"] = True
    try:
        for _ in range(INTENTOS):
            for it in _generar_una_vez(bolsa, contexto, nivel):
                r = int(getattr(it, "RarityLevel", 0))
                if r > mejor_r:
                    if mejor is not None:
                        mejor.Destroy()
                    mejor, mejor_r = it, r
                else:
                    it.Destroy()
            if mejor_r >= RAREZA_NARANJA:
                break
    finally:
        _estado["generando"] = False
    if mejor is not None and mejor_r < RAREZA_NARANJA:
        mejor.Destroy()
        return None
    return mejor


def _soltar(item: UObject, lugar: UObject, alto: float = 60) -> None:
    loc = lugar.Location
    item.DropFrom(unrealsdk.make_struct("Vector", X=loc.X, Y=loc.Y, Z=loc.Z + alto),
                  unrealsdk.make_struct("Vector", X=0, Y=0, Z=250))


# ---------------------------------------------------------------- jefes
JEFES = ("ninetoes", "nine_toes", "baronflynt", "flynt", "sledge", "krom", "reaver",
         "destroyer", "commandant", "steele", "tobbs", "mrshaw", "mr_shaw", "kobb", "henry",
         "rakkman", "lockjaw", "pinky", "digit", "crawmerax", "knoxx", "ned", "skagzilla", "scar",
         "bleeder", "moe", "marley", "weewee", "queen", "rakkhive", "rakkinishu", "slither", "mothrakk")


def _es_jefe(pawn: UObject) -> bool:
    try:
        if pawn.IsBoss():
            return True
    except Exception:
        pass
    nombre = pawn._path_name().lower()
    try:
        bal = pawn.BalanceDefinitionState.BalanceDefinition
        if bal:
            nombre += " " + bal._path_name().lower()
    except Exception:
        pass
    plano = nombre.replace("_", "")
    return any(j.replace("_", "") in plano for j in JEFES)


def _muerte(pawn: UObject) -> None:
    clave = pawn._path_name()
    if clave in _jefes_hechos or not _es_jefe(pawn):
        return
    _jefes_hechos.add(clave)
    if len(_jefes_hechos) > 500:
        _jefes_hechos.clear()
    arma = _naranja_de(BOLSA_ARMAS, pawn, _nivel_jugador(int(pawn.GetExpLevel())))
    if arma is not None:
        _soltar(arma, pawn)
        log(f"Jefe {clave}: arma legendaria", consola=True)


@hook("WillowGame.WillowAIPawn:Died", Type.POST)
def on_died(obj: UObject, _a: WrappedStruct, _r: Any, _f: BoundFunction) -> None:
    try:
        _muerte(obj)
    except Exception as e:
        log(f"ERROR jefe: {e!r}")


@hook("WillowGame.WillowAIPawn:PlayDying", Type.POST)
def on_playdying(obj: UObject, _a: WrappedStruct, _r: Any, _f: BoundFunction) -> None:
    try:
        _muerte(obj)
    except Exception as e:
        log(f"ERROR jefe: {e!r}")


# ---------------------------------------------------------------- cofres
def _cofre_de(pickup: UObject, args: WrappedStruct) -> UObject | None:
    for f in args._type._fields():
        v = getattr(args, f.Name, None)
        if isinstance(v, UObject) and "InteractiveObject" in v.Class.Name:
            return v
    base = getattr(pickup, "Base", None)
    if base is not None and "InteractiveObject" in base.Class.Name:
        return base
    return None


@hook("WillowGame.WillowPickup:AttachPickupToWorldBody", Type.POST)
def on_attach(obj: UObject, args: WrappedStruct, _r: Any, _f: BoundFunction) -> None:
    if _estado["generando"]:
        return
    try:
        inv = getattr(obj, "Inventory", None)
        if inv is None or "Weapon" not in inv.Class.Name:
            return
        cofre = _cofre_de(obj, args)
        if cofre is None:
            return
        clave = cofre._path_name()
        if clave in _cofres_hechos:
            return
        _cofres_hechos.add(clave)
        nivel = int(inv.ExpLevel) if getattr(inv, "ExpLevel", 0) else _nivel_jugador()
        arma = _naranja_de(BOLSA_ARMAS, cofre, nivel)
        if arma is not None:
            _soltar(arma, cofre, alto=100)
            log(f"Cofre {clave}: arma legendaria", consola=True)
    except Exception as e:
        log(f"ERROR cofre: {e!r}")


# ---------------------------------------------------------------- experiencia de misiones
@hook("WillowGame.WillowPlayerController:ConditionalGrantRewardsAndCompleteMission", Type.PRE)
def on_mision_pre(_o: UObject, _a: WrappedStruct, _r: Any, _f: BoundFunction) -> None:
    _estado["mision"] += 1


@hook("WillowGame.WillowPlayerController:ConditionalGrantRewardsAndCompleteMission", Type.POST)
def on_mision_post(_o: UObject, _a: WrappedStruct, _r: Any, _f: BoundFunction) -> None:
    _estado["mision"] = max(0, _estado["mision"] - 1)


@hook("WillowGame.WillowPlayerController:ExpEarn", Type.PRE)
def on_exp(obj: UObject, args: WrappedStruct, _r: Any, _f: BoundFunction) -> Any:
    if _estado.get("dando_xp"):
        return None
    es_mision = _estado["mision"] > 0
    mult = op_xp.value if es_mision else op_xp_general.value
    if mult == 1:
        return None
    try:
        valores = {f.Name: getattr(args, f.Name) for f in args._type._fields() if f.Name != "ReturnValue"}
        campo = next(f.Name for f in args._type._fields() if f.Class.Name == "IntProperty")
        antes = valores[campo]
        valores[campo] = max(0, int(round(antes * mult)))
        _estado["dando_xp"] = True
        try:
            obj.ExpEarn(**valores)
        finally:
            _estado["dando_xp"] = False
        if es_mision:
            log(f"Misión: experiencia {antes} -> {valores[campo]} (x{mult})", consola=True)
        return Block
    except Exception as e:
        _estado["dando_xp"] = False
        log(f"ERROR experiencia: {e!r}")
        return None


@hook("WillowGame.WillowGameInfo:AwardCombatExperience", Type.PRE)
def on_combate(obj: UObject, args: WrappedStruct, _r: Any, _f: BoundFunction) -> Any:
    mult = op_xp_general.value
    if _estado.get("dando_combate") or mult == 1:
        return None
    try:
        campos = [(f.Name, f.Class.Name) for f in args._type._fields() if f.Name != "ReturnValue"]
        valores = {n: getattr(args, n) for n, _ in campos}
        if not _estado.get("firma_combate"):
            _estado["firma_combate"] = True
            log(f"AwardCombatExperience({', '.join(f'{n}:{c}={valores[n]!r}' for n, c in campos)})"[:400])
        cambiado = False
        for n, c in campos:
            if c == "IntProperty" and "level" not in n.lower():
                valores[n] = int(round(valores[n] * mult))
                cambiado = True
            elif c == "FloatProperty" and "level" not in n.lower():
                valores[n] = valores[n] * mult
                cambiado = True
        if not cambiado:
            return None
        _estado["dando_combate"] = True
        try:
            obj.AwardCombatExperience(**valores)
        finally:
            _estado["dando_combate"] = False
        return Block
    except Exception as e:
        _estado["dando_combate"] = False
        log(f"ERROR experiencia combate: {e!r}")
        return None


def _al_desactivar() -> None:
    p = _estado["pawn"]
    if p is not None:
        try:
            base = p.Class.ClassDefaultObject
            p.GroundSpeed, p.JumpZ = base.GroundSpeed, base.JumpZ
        except Exception:
            pass
    _estado["pawn"] = None


build_mod(options=[op_vel, op_salto, op_xp, op_xp_general], on_disable=_al_desactivar)
