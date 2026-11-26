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
# Se guarda el valor "natural" que pone el juego (caminar, correr, Phase Walk...) y se
# multiplica ese. Si el juego cambia la velocidad, se toma el nuevo valor como natural.
TOPE_NATURAL = {"GroundSpeed": 4.0, "JumpZ": 1.5}  # máximo "normal" respecto al valor de fábrica
_mov: dict[str, dict[str, float]] = {}  # por pawn: valores naturales y aplicados


def _ajustar(pawn: UObject, prop: str, mult: float) -> None:
    d = _mov.setdefault(pawn._path_name(), {})
    actual = float(getattr(pawn, prop))
    if d.get("apl_" + prop) is None or abs(actual - d["apl_" + prop]) > 1:
        # el juego lo cambió (o es la primera vez); tope de seguridad contra acumulación
        base = float(getattr(pawn.Class.ClassDefaultObject, prop))
        tope = base * (TOPE_NATURAL[prop]) if base > 0 else actual
        d["nat_" + prop] = min(actual, tope)
    deseado = d["nat_" + prop] * mult
    if abs(actual - deseado) > 0.5:
        setattr(pawn, prop, deseado)
    d["apl_" + prop] = deseado


def _restaurar(pawn: UObject) -> None:
    d = _mov.get(pawn._path_name(), {})
    for prop in ("GroundSpeed", "JumpZ"):
        try:
            if d.get("nat_" + prop) is not None and abs(float(getattr(pawn, prop)) - d["apl_" + prop]) <= 1:
                setattr(pawn, prop, d["nat_" + prop])
        except Exception:
            pass


def _revisar_mapa(pc: UObject) -> None:
    """Los nombres internos (WillowAIPawn_N...) se repiten en cada mapa: se limpian las listas."""
    try:
        wi = pc.WorldInfo
        mapa = str(wi.GetMapName(True))
        t = float(wi.TimeSeconds)
    except Exception:
        return
    if mapa != _estado.get("mapa") or t < _estado.get("t", 0.0):
        _jefes_hechos.clear()
        _cofres_hechos.clear()
        if _estado.get("mapa") is not None:
            log(f"Mapa: {mapa}", consola=True)
        _estado["mapa"] = mapa
    _estado["t"] = t


@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def on_tick(obj: UObject, _a: WrappedStruct, _r: Any, _f: BoundFunction) -> None:
    _revisar_mapa(obj)
    pawn = obj.Pawn
    # Solo el personaje: nunca el vehículo (si no, se acumulaba al subir y bajar)
    if pawn is None or "Vehicle" in pawn.Class.Name:
        return
    try:
        if pawn is not _estado["pawn"]:
            if len(_mov) > 50:
                _mov.clear()
            _estado["pawn"] = pawn
        _ajustar(pawn, "GroundSpeed", op_vel.value)
        _ajustar(pawn, "JumpZ", op_salto.value)
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


def _naranja_segura(ruta: str, contexto: UObject, nivel: int) -> UObject | None:
    """A nivel bajo el juego no fabrica naranjas: se sube el nivel del arma hasta lograrlo."""
    for extra in (0, 3, 6, 10, 15, 20, 30, 40, 50):
        n = min(nivel + extra, 80)
        arma = _naranja_de(ruta, contexto, n)
        if arma is not None:
            if extra:
                log(f"Sin naranja a nivel {nivel}; lograda a nivel {n}", consola=True)
            return arma
        if n >= 80:
            break
    log(f"ERROR: no salió ninguna naranja (nivel {nivel})")
    return None


def _jugador_pawn() -> UObject | None:
    try:
        for pc in unrealsdk.find_all("WillowPlayerController", exact=False):
            if pc.Pawn is not None:
                return pc.Pawn
    except Exception:
        pass
    return None


def _soltar(item: UObject, lugar: UObject, alto: float = 40, hacia_jugador: float = 0) -> None:
    """Suelta el ítem casi quieto; opcionalmente corrido hacia el jugador (fuera del cofre)."""
    loc = lugar.Location
    x, y = loc.X, loc.Y
    pj = _jugador_pawn() if hacia_jugador else None
    if pj is not None:
        dx, dy = pj.Location.X - x, pj.Location.Y - y
        d = (dx * dx + dy * dy) ** 0.5
        if d > 1:
            paso = min(hacia_jugador, d * 0.5)
            x, y = x + dx / d * paso, y + dy / d * paso
    item.DropFrom(unrealsdk.make_struct("Vector", X=x, Y=y, Z=loc.Z + alto),
                  unrealsdk.make_struct("Vector", X=0, Y=0, Z=120))


# ---------------------------------------------------------------- jefes
JEFES = ("ninetoes", "nine_toes", "baronflynt", "flynt", "sledge", "krom", "reaver",
         "destroyer", "commandant", "steele", "tobbs", "mrshaw", "mr_shaw", "kobb", "henry",
         "rakkman", "lockjaw", "pinky", "digit", "crawmerax", "knoxx", "ned", "skagzilla", "scar",
         "bleeder", "moe", "marley", "weewee", "queen", "rakkhive", "rakkinishu", "slither", "mothrakk", "vaultboss", "mccloud", "bonehead", "madmel")


NO_JEFES = ("kamikaze", "spawn", "minion", "npc")


def _balance(pawn: UObject) -> str:
    try:
        bal = pawn.BalanceDefinitionState.BalanceDefinition
        if bal:
            return bal._path_name().lower()
    except Exception:
        pass
    return ""


def _es_jefe(pawn: UObject) -> bool:
    bal = _balance(pawn)
    if not bal:
        return False
    corto = bal.rsplit(".", 1)[-1].replace("_", "")  # solo el nombre, no el paquete
    if any(n in corto for n in NO_JEFES):
        return False
    if ".named." in bal:  # jefes con nombre (Sledge, Kobb, Flynt...)
        return True
    return any(j.replace("_", "") in corto for j in JEFES)


# ---------------------------------------------------------------- arma única del jefe
GENERICAS = ("weaponpools", "ammo", "money", "health", "shield", "grenade", "artifact",
             "classmod", "enemyuse", "itempools.loot")


def _bolsas_de(obj: UObject | None, prof: int = 0, vistos: set | None = None) -> list[UObject]:
    """Busca bolsas de botín (ItemPoolDefinition) dentro del balance/arquetipo del jefe."""
    vistos = set() if vistos is None else vistos
    res: list[UObject] = []

    def ver(v: Any, pr: int) -> None:
        if pr > 4:
            return
        if isinstance(v, UObject):
            if v.Class.Name == "ItemPoolDefinition":
                if v._path_name() not in vistos:
                    vistos.add(v._path_name())
                    res.append(v)
            elif pr < 2 and ("Balance" in v.Class.Name or "Pawn" in v.Class.Name) and v._path_name() not in vistos:
                vistos.add(v._path_name())
                for f in v.Class._fields():
                    try:
                        ver(getattr(v, f.Name), pr + 1)
                    except Exception:
                        pass
        elif isinstance(v, WrappedStruct):
            for f in v._type._fields():
                try:
                    ver(getattr(v, f.Name), pr + 1)
                except Exception:
                    pass
        elif not isinstance(v, (str, bytes, int, float, bool)) and v is not None:
            try:
                for x in v:
                    ver(x, pr + 1)
            except TypeError:
                pass

    ver(obj, prof)
    return res


def _bolsas_unicas(pawn: UObject) -> list[UObject]:
    bolsas: list[UObject] = []
    vistos: set = set()
    try:
        bolsas += _bolsas_de(pawn.BalanceDefinitionState.BalanceDefinition, 0, vistos)
    except Exception:
        pass
    try:
        bolsas += _bolsas_de(pawn.ObjectArchetype, 0, vistos)
    except Exception:
        pass
    unicas = []
    for b in bolsas:
        ruta = b._path_name().lower()
        if any(g in ruta for g in GENERICAS):
            continue
        armas = False
        try:
            for it in b.BalancedItems:
                inv = it.InvBalanceDefinition
                if inv is not None and "weap" in inv._path_name().lower():
                    armas = True
                    break
        except Exception:
            pass
        if armas or "customweap" in ruta:
            unicas.append(b)
    return unicas


def _mejor_de(bolsa: UObject, contexto: UObject, nivel: int, intentos: int = 60) -> UObject | None:
    mejor, mejor_r = None, -1
    _estado["generando"] = True
    try:
        for _ in range(intentos):
            for it in _generar_una_vez(bolsa, contexto, nivel):
                if "Weapon" not in it.Class.Name:
                    it.Destroy()
                    continue
                r = int(getattr(it, "RarityLevel", 0))
                if r > mejor_r:
                    if mejor is not None:
                        mejor.Destroy()
                    mejor, mejor_r = it, r
                else:
                    it.Destroy()
    finally:
        _estado["generando"] = False
    return mejor


def _muerte(pawn: UObject) -> None:
    clave = pawn._path_name()
    if clave in _jefes_hechos or not _es_jefe(pawn):
        return
    _jefes_hechos.add(clave)
    if len(_jefes_hechos) > 500:
        _jefes_hechos.clear()
    ctx = _jugador_pawn() or pawn
    nivel = _nivel_jugador(int(pawn.GetExpLevel()))
    arma, tipo = None, "legendaria al azar"
    try:
        unicas = _bolsas_unicas(pawn)
        log(f"  bolsas únicas: {[u._path_name() for u in unicas]}")
        for u in unicas:
            arma = _mejor_de(u, ctx, nivel)
            if arma is not None:
                tipo = f"arma única ({u._path_name()})"
                break
    except Exception as e:
        log(f"ERROR arma única: {e!r}")
    if arma is None:
        arma = _naranja_segura(BOLSA_ARMAS, ctx, nivel)
    if arma is not None:
        pj = _jugador_pawn()
        lugar = pawn
        if pj is not None:
            a, b = pj.Location, pawn.Location
            if ((a.X - b.X) ** 2 + (a.Y - b.Y) ** 2 + (a.Z - b.Z) ** 2) ** 0.5 > 1200:
                lugar = pj  # jefe lejos, en el aire o en lugar raro: el arma va junto al jugador
        _soltar(arma, lugar, alto=50, hacia_jugador=0 if lugar is pj else 90)
        log(f"Jefe {clave}: {tipo}", consola=True)
    else:
        log(f"ERROR: jefe {clave} sin arma", consola=True)


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
        nivel = _nivel_jugador(int(getattr(inv, "ExpLevel", 0) or 1))
        arma = _naranja_segura(BOLSA_ARMAS, cofre, nivel)
        if arma is not None:
            _soltar(arma, cofre, alto=50, hacia_jugador=90)
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
        _restaurar(p)
    _estado["pawn"] = None
    _mov.clear()


build_mod(options=[op_vel, op_salto, op_xp, op_xp_general], on_disable=_al_desactivar)
