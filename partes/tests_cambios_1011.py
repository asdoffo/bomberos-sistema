"""Tests de los cambios del 10/11: roles nuevos, sesión según el rol, cambio de contraseña, constancia,
salida con concurrencia, cierre con regreso, rodados sin N° repetido, móviles por categoría, E-100,
departamentos de capacitación, libro de guardia y los comandos de carga."""
import csv
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import roles
from . import tests as base  # sin importar las clases: si no, sus tests se correrían dos veces


def demo():
	call_command("cargar_catalogos", "--demo", stdout=StringIO())


def usuario_con(nombre, *grupos, clave="clave-segura-123"):
	user = get_user_model().objects.create_user(nombre, password=clave)
	user.groups.set(Group.objects.filter(name__in=grupos))
	return user


class RolesNuevosTests(TestCase):
	def setUp(self):
		call_command("cargar_catalogos", stdout=StringIO())

	def permisos(self, rol):
		return set(Group.objects.get(name=rol).permissions.values_list("codename", flat=True))

	def test_graduado_es_guardia_menos_anotar_en_el_libro(self):
		self.assertEqual(self.permisos(roles.GRADUADO), self.permisos(roles.GUARDIA) - {"add_anotacionlibroguardia"})
		self.assertIn("view_anotacionlibroguardia", self.permisos(roles.GRADUADO))

	def test_constancia_solo_jefe_y_estadistica(self):
		con = {r for r in roles.PRIORIDAD if "emitir_constancia" in self.permisos(r)}
		self.assertEqual(con, {roles.JEFE, roles.ESTADISTICA})

	def test_aspirante_e_invitado_solo_ven(self):
		for rol in (roles.ASPIRANTE, roles.INVITADO):
			self.assertEqual(self.permisos(rol), {"view_partesiniestro"}, rol)

	def test_libro_de_guardia(self):
		ven = {r for r in roles.PRIORIDAD if "view_anotacionlibroguardia" in self.permisos(r)}
		anotan = {r for r in roles.PRIORIDAD if "add_anotacionlibroguardia" in self.permisos(r)}
		corrigen = {r for r in roles.PRIORIDAD if "corregir_libro" in self.permisos(r)}
		self.assertEqual(ven, {roles.JEFE, roles.GRADUADO, roles.ESTADISTICA, roles.GUARDIA})
		self.assertEqual(anotan, {roles.JEFE, roles.GUARDIA})
		self.assertEqual(corrigen, {roles.JEFE})

	def test_graduado_ve_el_libro_pero_no_anota(self):
		self.client.force_login(usuario_con("grad", roles.GRADUADO))
		libro = self.client.get(reverse("libro_guardia"))
		self.assertEqual(libro.status_code, 200)
		self.assertFalse(libro.context["puede_anotar"])
		self.client.force_login(usuario_con("bomb", roles.BOMBERO))
		self.assertNotEqual(self.client.get(reverse("libro_guardia")).status_code, 200)

	def test_prioridad_de_roles(self):
		self.assertEqual(roles.rol_principal(usuario_con("leyba", roles.BOMBERO, roles.GUARDIA)), roles.GUARDIA)
		self.assertEqual(roles.rol_principal(usuario_con("doffo", roles.GRADUADO, roles.ESTADISTICA)), roles.ESTADISTICA)
		self.assertIsNone(roles.rol_principal(usuario_con("sinrol")))


class InvitadoNoCompletaTests(TestCase):
	setUp = base.FlujoParteTests.setUp

	def test_invitado_ve_pero_no_completa(self):
		self.client.force_login(usuario_con("invitado", roles.INVITADO))
		url = reverse("completar_parte", args=[self.parte.pk])
		detalle = self.client.get(reverse("detalle_parte", args=[self.parte.pk]))
		self.assertEqual(detalle.status_code, 200)
		self.assertNotContains(detalle, url)
		self.assertContains(detalle, "solo permite ver el parte")
		self.assertRedirects(self.client.get(url), reverse("detalle_parte", args=[self.parte.pk]))
		for vista in ("incendio", "nueva_negativa", "fotos"):
			self.assertRedirects(self.client.get(reverse(vista, args=[self.parte.pk])),
			                     reverse("detalle_parte", args=[self.parte.pk]), msg_prefix=vista)
		self.assertEqual(self.client.post(reverse("sincronizar_parte", args=[self.parte.pk])).status_code, 403)
		self.assertEqual(self.client.post(reverse("descargar_parte", args=[self.parte.pk])).status_code, 403)

	def test_aspirante_si_completa(self):
		self.client.force_login(usuario_con("aspirante", roles.ASPIRANTE))
		self.assertEqual(self.client.get(reverse("completar_parte", args=[self.parte.pk])).status_code, 200)


class SesionPorRolTests(TestCase):
	def setUp(self):
		call_command("cargar_catalogos", stdout=StringIO())

	def entrar(self, *grupos):
		usuario_con("u", *grupos)
		self.client.post(reverse("login"), {"username": "u", "password": "clave-segura-123"})

	def correr(self, clave, horas=0, minutos=0):
		sesion = self.client.session
		sesion[clave] = (timezone.now() - timedelta(hours=horas, minutes=minutos)).isoformat()
		sesion.save()

	def sigue(self):
		return self.client.get(reverse("menu")).status_code == 200

	def test_jefe_estadistica_graduado_y_aspirante_12_horas_desde_el_login(self):
		from .sesion import CLAVE_INICIO
		for rol in (roles.JEFE, roles.ESTADISTICA, roles.GRADUADO, roles.ASPIRANTE):
			with self.subTest(rol=rol):
				get_user_model().objects.filter(username="u").delete()
				self.entrar(rol)
				self.correr(CLAVE_INICIO, horas=11, minutos=50)
				self.assertTrue(self.sigue())  # la actividad no la estira
				self.correr(CLAVE_INICIO, horas=12, minutos=1)
				self.assertFalse(self.sigue())
				self.assertContains(self.client.get(reverse("login")), "Tu sesión venció. Volvé a ingresar.")

	def test_bombero_12_horas_sin_actividad(self):
		from .sesion import CLAVE_ACTIVIDAD, CLAVE_INICIO
		self.entrar(roles.BOMBERO)
		self.correr(CLAVE_INICIO, horas=30)
		self.correr(CLAVE_ACTIVIDAD, horas=11, minutos=50)
		self.assertTrue(self.sigue())  # entró hace 30 h, pero lo usó hace menos de 12
		ultima = self.client.session[CLAVE_ACTIVIDAD]
		self.assertGreater(ultima, (timezone.now() - timedelta(minutes=1)).isoformat())  # se volvió a contar
		self.correr(CLAVE_ACTIVIDAD, horas=12, minutos=1)
		self.assertFalse(self.sigue())

	def test_invitado_y_sin_rol_10_minutos(self):
		from .sesion import CLAVE_INICIO
		for grupos in ([roles.INVITADO], []):
			with self.subTest(grupos=grupos):
				get_user_model().objects.filter(username="u").delete()
				self.entrar(*grupos)
				self.correr(CLAVE_INICIO, minutos=9)
				self.assertTrue(self.sigue())
				self.correr(CLAVE_INICIO, minutos=11)
				self.assertFalse(self.sigue())

	def test_guardia_y_bombero_se_rige_por_el_cambio_de_guardia(self):
		from .sesion import CAMBIO_GUARDIA, CLAVE_REGLA
		self.entrar(roles.BOMBERO, roles.GUARDIA)
		self.assertEqual(self.client.session[CLAVE_REGLA][0], CAMBIO_GUARDIA)

	@mock.patch("django.conf.settings.SESION_MINUTOS_PRUEBA", 2)
	def test_minutos_de_prueba(self):
		from .sesion import CLAVE_INICIO
		self.entrar(roles.JEFE)
		self.correr(CLAVE_INICIO, minutos=3)
		self.assertFalse(self.sigue())

	def test_cambiar_la_contrasena_no_corta_ni_estira_la_sesion(self):
		from .sesion import CLAVE_INICIO
		self.entrar(roles.JEFE)
		self.assertContains(self.client.get(reverse("mi_perfil")), reverse("password_change"))
		self.correr(CLAVE_INICIO, horas=3)
		inicio = self.client.session[CLAVE_INICIO]
		self.assertContains(self.client.get(reverse("password_change")), "Cambiar contraseña")
		respuesta = self.client.post(reverse("password_change"), {
			"old_password": "clave-segura-123", "new_password1": "Otra-clave-larga-987",
			"new_password2": "Otra-clave-larga-987"})
		self.assertRedirects(respuesta, reverse("password_change_done"))
		self.assertTrue(self.sigue())
		self.assertEqual(self.client.session[CLAVE_INICIO], inicio)
		self.assertTrue(get_user_model().objects.get(username="u").check_password("Otra-clave-larga-987"))


class SinSenalConSesionVencidaTests(TestCase):
	"""Lo cargado sin señal no se pierde si la sesión venció: el envío no se acepta (va al login, sin JSON),
	el celular lo conserva y lo manda de nuevo después de volver a entrar."""
	setUp = base.FlujoParteTests.setUp
	datos_completar = base.FlujoParteTests.datos_completar
	estado_inicial = base.SinSenalTests.estado_inicial
	pares = staticmethod(base.SinSenalTests.pares)
	sincronizar = base.SinSenalTests.sincronizar

	def test_envio_con_sesion_vencida_y_despues_de_entrar(self):
		import uuid
		from .sesion import CLAVE_ACTIVIDAD, CLAVE_INICIO

		base = self.estado_inicial()
		bombero = usuario_con("bomb", roles.BOMBERO)
		self.bombero.usuario = bombero
		self.bombero.save()
		self.client.post(reverse("login"), {"username": "bomb", "password": "clave-segura-123"})
		sesion = self.client.session
		sesion[CLAVE_INICIO] = sesion[CLAVE_ACTIVIDAD] = (timezone.now() - timedelta(hours=13)).isoformat()
		sesion.save()
		envio = str(uuid.uuid4())

		vencida = self.sincronizar(base, envio=envio, observaciones="Cargado sin señal")
		self.assertEqual(vencida.status_code, 302)  # al login: el celular no recibe {"ok": true} y lo guarda
		self.parte.refresh_from_db()
		self.assertEqual(self.parte.observaciones, "")

		self.client.post(reverse("login"), {"username": "bomb", "password": "clave-segura-123"})
		otra_vez = self.sincronizar(base, envio=envio, observaciones="Cargado sin señal")
		self.assertEqual(otra_vez.json()["ok"], True)
		self.parte.refresh_from_db()
		self.assertEqual(self.parte.observaciones, "Cargado sin señal")


class PartesTests(TestCase):
	setUp = base.FlujoParteTests.setUp
	datos_completar = base.FlujoParteTests.datos_completar

	def test_salida_con_concurrencia_y_participacion(self):
		from .models import PartePersonal
		detalle = self.client.get(reverse("detalle_parte", args=[self.parte.pk]))
		self.assertContains(detalle, f'name="personal-conc_{self.bombero.pk}"')
		self.assertContains(detalle, f'name="personal-part_{self.bombero.pk}"')
		jefe = get_user_model().objects.get(username="jefe").integrante
		self.client.post(reverse("marcar_hora", args=[self.parte.pk, "salida"]), {
			"salida-vehiculo": self.movil.pk, "salida-autorizo": jefe.pk, "guardar": "1",
			"personal-integrantes": [self.bombero.pk], f"personal-conc_{self.bombero.pk}": "convocado",
			f"personal-part_{self.bombero.pk}": "chofer"})
		fila = PartePersonal.objects.get(parte=self.parte, integrante=self.bombero)
		self.assertEqual((fila.tipo_concurrencia, fila.tipo_participacion), ("convocado", "chofer"))
		self.parte.refresh_from_db()
		self.assertIsNone(self.parte.hora_salida)  # "guardar" no marca la hora

	def test_concurrencia_en_el_cuartel_primero_y_por_defecto(self):
		from .models import PartePersonal
		self.assertEqual(PartePersonal.Concurrencia.choices[0][0], "en_cuartel")
		self.assertEqual(PartePersonal(parte=self.parte, integrante=self.bombero).tipo_concurrencia, "en_cuartel")

	def test_no_cierra_sin_el_regreso(self):
		self.completar_con(hora_regreso="")
		self.assertIn("Marcá el regreso de la unidad antes de cerrar el parte.", self.parte.problemas_para_cerrar())

	def completar_con(self, **extra):
		self.client.post(reverse("corregir_parte", args=[self.parte.pk]), self.datos_completar(
			personal=[self.bombero], km_regreso=self.movil.kilometraje_actual + 5, **extra))
		self.parte.refresh_from_db()

	def test_rodado_repetido(self):
		datos = self.datos_completar(**{
			"rodados-TOTAL_FORMS": 2, "rodados-0-rodado_numero": 1, "rodados-0-marca": "Ford",
			"rodados-1-rodado_numero": 1, "rodados-1-marca": "Fiat"})
		respuesta = self.client.post(reverse("corregir_parte", args=[self.parte.pk]), datos)
		self.assertContains(respuesta, "El rodado N° 1 está repetido")
		self.assertEqual(self.parte.vehiculos_siniestrados.count(), 0)
		self.assertContains(self.client.get(reverse("corregir_parte", args=[self.parte.pk])), "bloquearRepetidos")

	def test_constancia_jefe_con_parte_cerrado(self):
		self.completar_con()
		base.FlujoParteTests.cerrar(self)
		self.parte.refresh_from_db()
		self.assertTrue(self.parte.esta_cerrado)
		self.client.login(username="jefe", password="bomberos2026")
		self.assertEqual(self.client.get(reverse("constancia_intervencion", args=[self.parte.pk])).status_code, 200)

	def test_menu_lateral(self):
		menu = self.client.get(reverse("menu"))
		for texto in ("Partes de intervención", "Partes de capacitación", "Partes de mantenimiento"):
			self.assertContains(menu, texto)


class MovilesPorCategoriaTests(TestCase):
	setUp = base.FlujoParteTests.setUp

	def test_agrupar_y_ordenar(self):
		from .models import TipoVehiculo, Vehiculo
		from .moviles import agrupar
		ambulancia, _ = TipoVehiculo.objects.get_or_create(descripcion="Ambulancia")
		raro, _ = TipoVehiculo.objects.get_or_create(descripcion="Tipo que no existe")
		comun = {"marca": "X", "modelo": "Y", "combustible": self.movil.combustible}
		Vehiculo.objects.create(legajo="10", tipo_vehiculo=ambulancia, **comun)
		Vehiculo.objects.create(legajo="4-B2011", tipo_vehiculo=ambulancia, **comun)
		Vehiculo.objects.create(legajo="4", tipo_vehiculo=ambulancia, **comun)
		Vehiculo.objects.create(legajo="99", tipo_vehiculo=raro, **comun)
		grupos = dict(agrupar(Vehiculo.objects.select_related("tipo_vehiculo")))
		self.assertEqual([v.legajo for v in grupos["Ambulancias"]], ["4", "10", "4-B2011"])
		self.assertIn("99", [v.legajo for v in grupos["Otras"]])

	def test_pantalla_y_selector_de_la_salida(self):
		moviles = self.client.get(reverse("moviles"))
		self.assertEqual(moviles.status_code, 200)
		self.assertTrue(moviles.context["categorias"])
		detalle = self.client.get(reverse("detalle_parte", args=[self.parte.pk]))
		self.assertContains(detalle, "<optgroup")


class CapacitacionDeDepartamentoTests(TestCase):
	def setUp(self):
		from .models import Capacitacion
		base.FlujoParteTests.setUp(self)
		Capacitacion.objects.all().delete()
		self.client.login(username="estadistica", password="bomberos2026")

	def datos(self, **extra):
		datos = {"datos": "Curso", "fecha": "2026-05-10", "modalidad": "interna", "sentido": "dictada",
		         "nivel": "cuartel", "lista_destinos": ["aspirantes_3"]}
		datos.update(extra)
		return datos

	def test_departamentos_cargados_por_la_migracion(self):
		from .models import Departamento
		self.assertEqual(Departamento.objects.count(), 13)
		self.assertTrue(Departamento.objects.filter(codigo="era", nombre="Dto. ERA").exists())

	def test_sin_tildar_no_guarda_departamentos(self):
		from .models import Capacitacion
		self.client.post(reverse("nueva_capacitacion"), self.datos(departamentos=["k9"]))
		self.assertEqual(Capacitacion.objects.get().destinos, "aspirantes_3")

	def test_tildado_pide_departamento_y_lo_guarda(self):
		from .models import Capacitacion
		sin = self.client.post(reverse("nueva_capacitacion"), self.datos(de_departamento="on"))
		self.assertContains(sin, "Elegí el departamento")
		self.client.post(reverse("nueva_capacitacion"), self.datos(de_departamento="on", departamentos=["k9"]))
		capacitacion = Capacitacion.objects.get()
		self.assertEqual(capacitacion.lista_destinos(), ["Aspirantes 3er año", "Dto. K9"])
		edicion = self.client.get(reverse("editar_capacitacion", args=[capacitacion.pk]))
		self.assertTrue(edicion.context["form"].initial["de_departamento"])

	def test_destino_retirado_se_muestra_y_se_conserva(self):
		from .models import Capacitacion
		self.client.post(reverse("nueva_capacitacion"), self.datos())
		capacitacion = Capacitacion.objects.get()
		Capacitacion.objects.filter(pk=capacitacion.pk).update(destinos="nivel_4,aspirantes_3")
		capacitacion.refresh_from_db()
		self.assertEqual(capacitacion.lista_destinos(), ["Aspirantes 3er año", "IV Nivel"])
		self.client.post(reverse("editar_capacitacion", args=[capacitacion.pk]), self.datos())
		capacitacion.refresh_from_db()
		self.assertIn("nivel_4", capacitacion.codigos_destinos)
		self.assertNotContains(self.client.get(reverse("nueva_capacitacion")), "1° domingo")


class CrearUsuariosIntegrantesTests(TestCase):
	def setUp(self):
		from .models import EstadoIntegrante, Integrante, NumeroMerito, Rango
		call_command("cargar_catalogos", stdout=StringIO())
		self.anio = timezone.localdate().year
		rango = Rango.objects.first()
		self.integrantes = {}
		for n, (numero, apellido, nombre, estado) in enumerate([
			(101, "Nicola", "Gustavo", "Activo"), (114, "Doffo", "Augusto", "Activo"),
			(212, "Leyba", "Gastón", "Activo"), (233, "Perez", "Federico", "Activo"),
			(276, "Perez", "Florencia", "Activo"), (324, "De los Dolores", "Joaquín", "Aspirante"),
			(318, "Acuña", "Emanuel", "Aspirante"), (402, "Salgarolo", "Rita", "Auxiliar"),
			(609, "Taberna", "D", "Retirado"), (None, "Sin", "Numero", "Activo"),
		]):
			estado_obj, _ = EstadoIntegrante.objects.get_or_create(nombre_estado=estado)
			i = Integrante.objects.create(legajo=f"L{n}", apellido=apellido, nombre=nombre, dni=f"3000000{n}",
			                              rango=rango, estado=estado_obj)
			if numero:
				NumeroMerito.objects.create(integrante=i, anio=self.anio, numero_merito=numero, rango=rango)
			self.integrantes[numero] = i
		self.parches = [
			mock.patch("partes.management.commands.crear_usuarios_integrantes.GUARDIAS",
			           [(212, "Leyba"), (324, "De los Dolores")]),
			mock.patch("partes.management.commands.crear_usuarios_integrantes.ESTADISTICA", [(114, "Doffo")]),
		]
		for p in self.parches:
			p.start()
			self.addCleanup(p.stop)

	def correr(self, *opciones):
		salida = StringIO()
		call_command("crear_usuarios_integrantes", *opciones, stdout=salida)
		return salida.getvalue()

	def roles_de(self, numero):
		self.integrantes[numero].refresh_from_db()
		return set(self.integrantes[numero].usuario.groups.values_list("name", flat=True))

	def test_roles_usuarios_y_claves(self):
		salida = self.correr()
		esperado = {101: {roles.JEFE}, 114: {roles.GRADUADO, roles.ESTADISTICA},
		            212: {roles.BOMBERO, roles.GUARDIA}, 324: {roles.ASPIRANTE, roles.GUARDIA},
		            318: {roles.ASPIRANTE}, 402: {roles.INVITADO}, 609: {roles.INVITADO}}
		for numero, grupos in esperado.items():
			self.assertEqual(self.roles_de(numero), grupos, numero)
		usuarios = {n: self.integrantes[n].usuario.username for n in esperado}
		self.assertEqual(usuarios[114], "adoffo")
		self.assertEqual(usuarios[324], "jdelosdolores")
		self.assertEqual(usuarios[318], "eacuna")
		self.assertEqual(usuarios[212], "gleyba")
		self.assertEqual(usuarios[609], "dtaberna")
		self.assertTrue(self.integrantes[114].usuario.check_password("adoffo_358"))
		# Repetidos: a los dos se les agrega el N° de mérito.
		self.assertEqual(self.roles_de(233), {roles.BOMBERO})
		self.integrantes[276].refresh_from_db()
		self.assertEqual((self.integrantes[233].usuario.username, self.integrantes[276].usuario.username),
		                 ("fperez233", "fperez276"))
		self.integrantes[None].refresh_from_db()
		self.assertIsNone(self.integrantes[None].usuario)
		self.assertIn("Sin, Numero: sin N° de mérito", salida)
		self.assertIn("Taberna", salida.split("Revisar el nombre")[1])

	def test_dry_run_no_guarda_y_es_idempotente(self):
		self.correr("--dry-run")
		self.assertEqual(get_user_model().objects.count(), 0)
		self.correr()
		cantidad = get_user_model().objects.count()
		self.assertIn("Ya tenían usuario", self.correr())
		self.assertEqual(get_user_model().objects.count(), cantidad)

	def test_saca_los_usuarios_de_prueba_y_deja_admin_sin_integrante(self):
		from .models import Firma
		admin = get_user_model().objects.create_superuser("admin", "", "clave-admin-123")
		self.integrantes[114].usuario = admin
		self.integrantes[114].save()
		guardia1 = usuario_con("guardia1", roles.GUARDIA)
		self.integrantes[101].usuario = guardia1
		self.integrantes[101].save()
		Firma.sellar(guardia1, Firma.Accion.CIERRE_PARTE, self.integrantes[101], {"x": 1})
		usuario_con("bombero", roles.BOMBERO)

		salida = self.correr()

		guardia1.refresh_from_db()
		self.assertFalse(guardia1.is_active)  # firmó: no se borra
		self.assertFalse(guardia1.groups.exists())
		self.assertFalse(get_user_model().objects.filter(username="bombero").exists())
		self.assertIn("bombero: borrado", salida)
		admin.refresh_from_db()
		self.assertTrue(admin.is_superuser)
		self.assertFalse(hasattr(admin, "integrante"))
		self.assertEqual(self.integrantes[114].__class__.objects.get(pk=self.integrantes[114].pk).usuario.username,
		                 "adoffo")
		self.assertEqual(self.roles_de(101), {roles.JEFE})

	def test_frena_si_no_encuentra_a_un_guardia(self):
		with mock.patch("partes.management.commands.crear_usuarios_integrantes.GUARDIAS", [(999, "Nadie")]):
			with self.assertRaises(CommandError):
				self.correr()
		self.assertEqual(get_user_model().objects.count(), 0)

	def test_salida_csv(self):
		with tempfile.TemporaryDirectory() as carpeta:
			ruta = Path(carpeta) / "usuarios.csv"
			self.correr("--salida", str(ruta))
			with open(ruta, encoding="utf-8-sig") as archivo:
				filas = list(csv.reader(archivo))
		self.assertEqual(filas[0], ["N° mérito", "Apellido y nombre", "Usuario", "Clave inicial", "Roles"])
		self.assertIn(["114", "Doffo, Augusto", "adoffo", "adoffo_358", "Graduado + Estadística"], filas)


class ActualizarVehiculosTests(TestCase):
	def setUp(self):
		demo()

	def escribir(self, carpeta, nombre, encabezado, filas):
		with open(Path(carpeta) / nombre, "w", encoding="utf-8", newline="") as archivo:
			escritor = csv.writer(archivo)
			escritor.writerow(encabezado)
			escritor.writerows(filas)

	def test_crea_actualiza_y_no_baja_el_km(self):
		from .models import Combustible, TipoVehiculo, Vehiculo
		movil = Vehiculo.objects.first()
		Vehiculo.objects.filter(pk=movil.pk).update(kilometraje_actual=5000)
		comb, tipo = Combustible.objects.first(), TipoVehiculo.objects.first()
		with tempfile.TemporaryDirectory() as carpeta:
			self.escribir(carpeta, "partes_combustible_1.csv", ["id", "descripcion"], [[comb.pk, comb.descripcion], [900, "Hidrógeno"]])
			self.escribir(carpeta, "partes_tipovehiculo_1.csv", ["id", "descripcion"], [[tipo.pk, "Tipo renombrado"]])
			encabezado = ["id", "legajo", "marca", "modelo", "patente", "anio", "fecha_adquisicion", "kilometraje_actual",
			              "fecha_vto_verificacion", "es_propiedad_cuerpo", "en_servicio", "dado_de_baja",
			              "combustible_id", "tipo_vehiculo_id"]
			self.escribir(carpeta, "partes_vehiculo_1.csv", encabezado, [
				[movil.pk, movil.legajo, "Iveco", "Nuevo", "", 2020, "2020-01-02", 100, "", 1, 1, 0, 900, tipo.pk],
				[950, "77", "Ford", "Ranger", "", 2022, "", 0, "2027-01-01", 0, 1, 0, comb.pk, tipo.pk]])
			call_command("actualizar_vehiculos", "--dir", carpeta, "--dry-run", stdout=StringIO())
			self.assertFalse(Vehiculo.objects.filter(pk=950).exists())
			salida = StringIO()
			call_command("actualizar_vehiculos", "--dir", carpeta, stdout=salida)
		movil.refresh_from_db()
		self.assertEqual((movil.marca, movil.modelo, movil.combustible_id), ("Iveco", "Nuevo", 900))
		self.assertEqual(movil.kilometraje_actual, 5000)  # no se baja
		self.assertIn("el km no se baja", salida.getvalue())
		nuevo = Vehiculo.objects.get(pk=950)
		self.assertEqual((nuevo.legajo, nuevo.es_propiedad_cuerpo, str(nuevo.fecha_vto_verificacion)),
		                 ("77", False, "2027-01-01"))
		self.assertIsNone(nuevo.fecha_adquisicion)
		self.assertEqual(TipoVehiculo.objects.get(pk=tipo.pk).descripcion, "Tipo renombrado")

	def test_si_los_ids_no_coinciden_no_pisa_nada(self):
		# Los CSV del proyecto salen de la base del cuartel: sobre la demo (otros ids) chocan los legajos.
		from .models import Vehiculo
		antes = list(Vehiculo.objects.values_list("pk", "legajo", "marca"))
		with self.assertRaisesMessage(CommandError, "no se cargó nada"):
			call_command("actualizar_vehiculos", "--dir", "partes/datos/vehiculos", stdout=StringIO())
		self.assertEqual(list(Vehiculo.objects.values_list("pk", "legajo", "marca")), antes)


class CargarE100Tests(TestCase):
	def setUp(self):
		demo()

	def test_vincula_y_no_duplica(self):
		from .models import Integrante, PersonalE100
		rossi = Integrante.objects.get(legajo="1003")
		numero = rossi.numero_merito_en(timezone.localdate().year)
		with mock.patch("partes.management.commands.cargar_e100.PERSONAL", [("ROSSI Mateo", numero, "Rossi")]):
			call_command("cargar_e100", "--dry-run", stdout=StringIO())
			self.assertFalse(PersonalE100.objects.filter(apellido_nombre="ROSSI Mateo").exists())
			call_command("cargar_e100", stdout=StringIO())
			call_command("cargar_e100", stdout=StringIO())
		e100 = PersonalE100.objects.get(apellido_nombre="ROSSI Mateo")
		self.assertEqual((e100.integrante, str(e100.fecha_asignacion), e100.activo), (rossi, "2026-11-01", True))

	def test_frena_si_el_numero_no_es_de_esa_persona(self):
		with mock.patch("partes.management.commands.cargar_e100.PERSONAL", [("X", 1, "Nadie")]):
			with self.assertRaises(CommandError):
				call_command("cargar_e100", stdout=StringIO())
