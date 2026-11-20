from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AuthenticationFlowTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(
			username="bombero",
			password="clave-segura-123",
		)

	def test_anonymous_user_is_redirected_to_login(self):
		response = self.client.get(reverse("listado_partes"))

		self.assertRedirects(
			response,
			f"{reverse('login')}?next={reverse('listado_partes')}",
		)

	def test_root_shows_menu(self):
		self.client.force_login(self.user)

		response = self.client.get("/")

		self.assertContains(response, "Mi perfil")
		self.assertNotContains(response, reverse("crear_parte"))  # sin rol no puede crear

	def test_authenticated_user_can_reach_listing(self):
		self.client.force_login(self.user)

		response = self.client.get(reverse("listado_partes"))

		self.assertEqual(response.status_code, 200)

	def test_logout_uses_post_and_redirects_to_login(self):
		self.client.force_login(self.user)

		response = self.client.post(reverse("logout"))

		self.assertRedirects(response, reverse("login"))
		self.assertNotIn("_auth_user_id", self.client.session)


class NumeroCorrelativoTests(TestCase):
	def setUp(self):
		from datetime import date
		from .models import EstadoIntegrante, EstadoParte, Integrante, Motivo, Rango, TipoSiniestro

		self.date = date
		integrante = Integrante.objects.create(
			legajo="1", apellido="Pérez", nombre="Juan", dni="1",
			rango=Rango.objects.create(nombre_rango="Bombero", orden_jerarquico=10),
			estado=EstadoIntegrante.objects.create(nombre_estado="Activo"),
		)
		self.datos = {
			"ubicacion_calle": "Belgrano",
			"tipo": TipoSiniestro.objects.create(descripcion="Incendio"),
			"motivo": Motivo.objects.create(descripcion="Vivienda"),
			"estado_parte": EstadoParte.objects.create(nombre_estado="Iniciado"),
			"guardia": integrante,
		}

	def crear(self, fecha):
		from .models import ParteSiniestro

		return ParteSiniestro.objects.create(fecha=fecha, **self.datos)

	def test_correlativo_es_consecutivo_dentro_del_anio(self):
		self.assertEqual(self.crear(self.date(2026, 3, 1)).numero_correlativo, 1)
		self.assertEqual(self.crear(self.date(2026, 3, 2)).numero_correlativo, 2)

	def test_correlativo_reinicia_por_anio_de_la_fecha(self):
		self.crear(self.date(2026, 12, 31))
		parte = self.crear(self.date(2027, 1, 1))

		self.assertEqual((parte.numero_correlativo, parte.anio), (1, 2027))

	def test_borrar_un_parte_no_repite_numero(self):
		primero = self.crear(self.date(2026, 3, 1))
		self.crear(self.date(2026, 3, 2))
		primero.delete()

		self.assertEqual(self.crear(self.date(2026, 3, 3)).numero_correlativo, 3)

	def test_editar_no_cambia_el_numero(self):
		parte = self.crear(self.date(2026, 3, 1))
		parte.observaciones = "editado"
		parte.save()

		self.assertEqual(parte.numero_correlativo, 1)


class AdminSmokeTests(TestCase):
	def test_todas_las_pantallas_del_admin_cargan(self):
		from django.contrib import admin

		superusuario = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
		self.client.force_login(superusuario)
		for modelo, modelo_admin in admin.site._registry.items():
			opts = modelo._meta
			vistas = ["changelist"]
			# Las firmas no se agregan a mano: su alta da 403 a propósito.
			if modelo_admin.has_add_permission(type("Pedido", (), {"user": superusuario})()):
				vistas.append("add")
			for vista in vistas:
				url = reverse(f"admin:{opts.app_label}_{opts.model_name}_{vista}")
				with self.subTest(url=url):
					self.assertEqual(self.client.get(url).status_code, 200)
		self.assertEqual(self.client.get(reverse("admin:partes_firma_add")).status_code, 403)


class CargarCatalogosTests(TestCase):
	def test_se_puede_correr_dos_veces_sin_duplicar(self):
		from io import StringIO
		from django.core.management import call_command
		from .models import Damnificado, ParteSiniestro, Rango

		call_command("cargar_catalogos", "--demo", stdout=StringIO())
		call_command("cargar_catalogos", "--demo", stdout=StringIO())

		self.assertEqual(Rango.objects.count(), 18)
		self.assertEqual(ParteSiniestro.objects.count(), 2)
		self.assertEqual(Damnificado.objects.count(), 2)
		self.assertTrue(self.client.login(username="guardia", password="bomberos2026"))


class MenuYPerfilTests(TestCase):
	def setUp(self):
		from io import StringIO
		from django.core.management import call_command

		call_command("cargar_catalogos", "--demo", stdout=StringIO())

	def test_login_lleva_al_menu(self):
		response = self.client.post(reverse("login"), {"username": "guardia", "password": "bomberos2026"})

		self.assertRedirects(response, reverse("menu"))

	def test_perfil_muestra_datos_y_numero_de_merito(self):
		self.client.login(username="guardia", password="bomberos2026")

		response = self.client.get(reverse("mi_perfil"))

		self.assertContains(response, "Lucía Gómez")
		self.assertContains(response, "115")

	def test_perfil_sin_integrante_vinculado(self):
		get_user_model().objects.create_user("suelto", password="x")
		self.client.login(username="suelto", password="x")

		response = self.client.get(reverse("mi_perfil"))

		self.assertContains(response, "no está vinculado")


class ExpiracionTurnoTests(TestCase):
	"""El guardia: la sesión vence en el cambio de guardia (06:00), haya actividad o no (10/11)."""

	def setUp(self):
		from django.contrib.auth.models import Group
		from .roles import GUARDIA

		guardia = get_user_model().objects.create_user("guardia", password="clave-segura-123")
		guardia.groups.add(Group.objects.create(name=GUARDIA))
		self.client.post(reverse("login"), {"username": "guardia", "password": "clave-segura-123"})

	def login_a_las(self, cuando):
		from .sesion import CLAVE_INICIO
		sesion = self.client.session
		sesion[CLAVE_INICIO] = cuando.isoformat()
		sesion.save()

	def test_proximo_cambio_de_guardia(self):
		from datetime import datetime
		from django.utils import timezone
		from .sesion import proximo_cambio_guardia

		zona = timezone.get_current_timezone()
		self.assertEqual(proximo_cambio_guardia(datetime(2026, 11, 10, 5, 50, tzinfo=zona)),
		                 datetime(2026, 11, 10, 6, 0, tzinfo=zona))
		self.assertEqual(proximo_cambio_guardia(datetime(2026, 11, 10, 6, 1, tzinfo=zona)),
		                 datetime(2026, 11, 11, 6, 0, tzinfo=zona))

	def test_actividad_no_extiende_el_turno(self):
		from .sesion import CLAVE_INICIO

		inicio = self.client.session[CLAVE_INICIO]
		response = self.client.get(reverse("menu"))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(self.client.session[CLAVE_INICIO], inicio)
		self.assertContains(response, "Tu sesión dura hasta las 06:00")

	def test_sesion_vence_en_el_cambio_de_guardia(self):
		from datetime import timedelta
		from django.utils import timezone
		from .sesion import proximo_cambio_guardia

		# Entró 10 minutos antes del último cambio de guardia: ya venció.
		ultimo = proximo_cambio_guardia(timezone.now()) - timedelta(days=1)
		self.login_a_las(ultimo - timedelta(minutes=10))

		response = self.client.get(reverse("menu"))

		self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
		self.assertNotIn("_auth_user_id", self.client.session)
		self.assertContains(self.client.get(reverse("login")), "Tu sesión venció por cambio de guardia")

	def test_la_cookie_dura_mas_que_el_turno(self):
		# Si la cookie venciera junto con el turno, el navegador la borraría y no se vería el aviso.
		from django.utils import timezone
		from .sesion import proximo_cambio_guardia
		falta = (proximo_cambio_guardia(timezone.now()) - timezone.now()).total_seconds()
		self.assertGreater(self.client.session.get_expiry_age(), falta + 23 * 60 * 60)


class FlujoParteTests(TestCase):
	"""Alta → completar → cerrar, con las reglas de negocio del README (sección 5)."""

	def setUp(self):
		from io import StringIO
		from django.core.management import call_command
		from .models import Integrante, TipoSiniestro, Motivo, Vehiculo

		call_command("cargar_catalogos", "--demo", stdout=StringIO())
		self.client.login(username="guardia", password="bomberos2026")
		self.movil = Vehiculo.objects.get(legajo="2")
		self.bombero = Integrante.objects.get(legajo="1003")
		respuesta = self.client.post(reverse("crear_parte"), {
			"direccion_input": "Belgrano 1450", "medio_llamado": "telefonico", "caracteristica": "353",
			"telefono_numero": "4123456", "barrio": "Centro",
			"tipo": TipoSiniestro.objects.get(descripcion="Incendio").pk,
			"motivo": Motivo.objects.get(codigo="101").pk,
		})
		from datetime import time
		from .models import ParteSiniestro
		# La hora de llamada sale del reloj: se fija para poder medir los tiempos operativos.
		ParteSiniestro.objects.filter(ubicacion_calle="Belgrano").update(hora_llamada=time(10, 0))
		self.parte = ParteSiniestro.objects.get(ubicacion_calle="Belgrano")
		self.respuesta_alta = respuesta

	def datos_completar(self, personal=(), **extra):
		datos = {
			"tipo": self.parte.tipo_id, "motivo": self.parte.motivo_id,
			"ubicacion_calle": self.parte.ubicacion_calle, "ubicacion_numero": self.parte.ubicacion_numero,
			"hora_llamada": "10:00",
			"vehiculo": self.movil.pk, "hora_salida": "10:03",
			"hora_llegada": "10:12", "hora_regreso": "11:30", "km_regreso": "",
			"combustible_nivel": "", "observaciones": "",
			"personal-integrantes": [i.pk for i in personal],
		}
		for prefijo in ("rodados", "damnificados"):
			datos[f"{prefijo}-TOTAL_FORMS"] = 0
			datos[f"{prefijo}-INITIAL_FORMS"] = 0
		datos.update(extra)
		return datos

	def completar(self, **kwargs):
		return self.client.post(reverse("completar_parte", args=[self.parte.pk]), self.datos_completar(**kwargs))

	def cerrar(self):
		self.client.post(reverse("cerrar_parte", args=[self.parte.pk]), {"clave": "bomberos2026"})
		self.parte.refresh_from_db()
		self.movil.refresh_from_db()

	def test_alta_separa_direccion_y_lleva_al_detalle(self):
		self.assertRedirects(self.respuesta_alta, reverse("detalle_parte", args=[self.parte.pk]))
		self.assertEqual(self.parte.ubicacion_numero, "1450")
		self.assertEqual(self.parte.guardia.legajo, "1001")

	def test_pantallas_cargan(self):
		from .models import VehiculoSiniestrado

		rodado = VehiculoSiniestrado.objects.get()
		for url in (reverse("listado_partes"), reverse("detalle_parte", args=[rodado.parte.pk]),
		            reverse("completar_parte", args=[self.parte.pk]),
		            reverse("damnificados", args=[rodado.parte.pk, rodado.pk])):
			with self.subTest(url=url):
				self.assertEqual(self.client.get(url).status_code, 200)
		self.assertContains(self.client.get(reverse("detalle_parte", args=[rodado.parte.pk])),
		                    "triage-amarillo")

	def test_completar_no_cambia_el_llamado_ni_las_horas(self):
		respuesta = self.completar(medio_llamado="radio", seguir="1")

		self.assertRedirects(respuesta, reverse("listado_partes"))  # Guardar y volver a Partes
		self.parte.refresh_from_db()
		self.assertEqual(self.parte.medio_llamado, "telefonico")
		self.assertEqual(self.parte.telefono_origen, "353 4123456")
		self.assertIsNone(self.parte.hora_salida)
		pantalla = self.client.get(reverse("completar_parte", args=[self.parte.pk]))
		self.assertNotContains(pantalla, 'name="medio_llamado"')
		self.assertContains(pantalla, reverse("corregir_parte", args=[self.parte.pk]))

	def test_corregir_datos_es_solo_del_guardia(self):
		from django.contrib.auth.models import User

		usuario = User.objects.create_user("rossi", password="clave-segura-123")
		self.bombero.usuario = usuario
		self.bombero.save()
		self.client.force_login(usuario)

		respuesta = self.client.post(reverse("corregir_parte", args=[self.parte.pk]),
		                             self.datos_completar(medio_llamado="radio"))

		self.assertRedirects(respuesta, reverse("detalle_parte", args=[self.parte.pk]))
		self.parte.refresh_from_db()
		self.assertEqual(self.parte.medio_llamado, "telefonico")

	def test_completar_guarda_personal_y_pasa_a_en_curso(self):
		respuesta = self.completar(personal=[self.bombero], _modo="corregir")  # las horas, desde Corregir datos

		self.assertRedirects(respuesta, reverse("detalle_parte", args=[self.parte.pk]))
		self.parte.refresh_from_db()
		self.assertEqual(self.parte.estado_parte.nombre_estado, "En curso")
		self.assertEqual(self.parte.personal.get().integrante, self.bombero)
		self.assertEqual(self.parte.minutos_respuesta, 12)

	def test_si_la_unidad_no_salio_se_vacian_horarios_y_km(self):
		datos = self.datos_completar(km_regreso="999999", salida_cancelada="on", _modo="corregir")
		self.client.post(reverse("completar_parte", args=[self.parte.pk]), datos)

		self.parte.refresh_from_db()
		self.assertFalse(self.parte.unidad_salio)
		self.assertIsNone(self.parte.hora_salida)
		self.assertIsNone(self.parte.km_regreso)

	def test_km_de_regreso_menor_al_del_movil_es_error(self):
		respuesta = self.completar(personal=[self.bombero], km_regreso=self.movil.kilometraje_actual - 10)

		self.assertEqual(respuesta.status_code, 200)
		self.assertContains(respuesta, "Debe ser mayor al último km anotado")

	def test_no_se_cierra_sin_personal(self):
		self.completar(km_regreso=self.movil.kilometraje_actual + 25)
		self.cerrar()

		self.assertEqual(self.parte.estado_parte.nombre_estado, "En curso")

	def test_cierre_actualiza_km_del_movil_y_bloquea_edicion(self):
		km = self.movil.kilometraje_actual + 25
		self.completar(personal=[self.bombero], km_regreso=km, _modo="corregir")
		self.cerrar()

		self.assertEqual(self.parte.estado_parte.nombre_estado, "Cerrado")
		self.assertEqual(self.parte.cerro.legajo, "1001")
		self.assertEqual(self.movil.kilometraje_actual, km)
		# Cerrado no lo corrige nadie, ni el guardia.
		self.assertRedirects(self.client.get(reverse("completar_parte", args=[self.parte.pk])),
		                     reverse("detalle_parte", args=[self.parte.pk]))
		get_user_model().objects.create_user("bombero2", password="clave-segura-123")
		self.client.login(username="bombero2", password="clave-segura-123")
		respuesta = self.client.get(reverse("completar_parte", args=[self.parte.pk]), follow=True)
		self.assertRedirects(respuesta, reverse("listado_partes"))  # el cerrado ni siquiera lo ve

	def test_cargar_damnificado_con_triage(self):
		from .models import VehiculoSiniestrado

		rodado = VehiculoSiniestrado.objects.create(parte=self.parte, rodado_numero=1, marca="Ford")
		self.client.post(reverse("damnificados", args=[self.parte.pk, rodado.pk]), {
			"damnificados-TOTAL_FORMS": 1, "damnificados-INITIAL_FORMS": 0,
			"damnificados-0-apellido_nombre": "Paz, Juan", "damnificados-0-rol": "conductor",
			"damnificados-0-triage": "rojo",
		})

		self.assertEqual(rodado.damnificados.get().triage, "rojo")
		self.assertTrue(rodado.damnificados.get().herido)  # con un color, queda herido


class MedicoYFotosTests(TestCase):
	def setUp(self):
		from io import StringIO
		from django.core.management import call_command
		from .models import Damnificado

		call_command("cargar_catalogos", "--demo", stdout=StringIO())
		self.client.login(username="guardia", password="bomberos2026")
		self.damnificado = Damnificado.objects.get(apellido_nombre="López, Tomás")
		self.parte = self.damnificado.parte

	def test_cargar_datos_de_incendio(self):
		self.client.post(reverse("incendio", args=[self.parte.pk]),
		                 {"lo_danado": ["Casa", "Galpón"], "descripcion": "Cocina"})

		self.assertEqual(self.parte.incendio.tipo_bien_danado, "Casa, Galpón")

	def test_negativa_desde_damnificado_precarga_datos_y_genera_constancia(self):
		url = reverse("nueva_negativa", args=[self.parte.pk]) + f"?damnificado={self.damnificado.pk}"
		self.assertContains(self.client.get(url), "López, Tomás")

		respuesta = self.client.post(url, {
			"fecha": "2026-10-15", "damnificado": self.damnificado.pk, "tipo_firmante": "responsable",
			"nombre_firmante": "López, Ana", "dni_firmante": "30999888", "relacion_paciente": "Madre",
		})

		negativa = self.parte.negativas.get()
		self.assertRedirects(respuesta, reverse("constancia_negativa", args=[negativa.pk]))
		constancia = self.client.get(reverse("constancia_negativa", args=[negativa.pk]))
		self.assertContains(constancia, "López, Ana")
		self.assertContains(constancia, "Madre")

	def test_negativa_de_responsable_exige_vinculo(self):
		respuesta = self.client.post(reverse("nueva_negativa", args=[self.parte.pk]), {
			"fecha": "2026-10-15", "tipo_firmante": "responsable",
			"nombre_firmante": "X", "dni_firmante": "1",
		})

		self.assertContains(respuesta, "Indicá el vínculo")

	def test_subir_fotos_y_rechazar_lo_que_no_es_imagen(self):
		import tempfile
		from django.core.files.uploadedfile import SimpleUploadedFile
		from django.test import override_settings

		with tempfile.TemporaryDirectory() as carpeta, override_settings(MEDIA_ROOT=carpeta):
			url = reverse("fotos", args=[self.parte.pk])
			self.client.post(url, {"archivos": [
				SimpleUploadedFile("a.jpg", b"jpg", content_type="image/jpeg"),
				SimpleUploadedFile("b.png", b"png", content_type="image/png"),
			]})
			respuesta = self.client.post(url, {"archivos": [
				SimpleUploadedFile("c.pdf", b"pdf", content_type="application/pdf")]})

			self.assertEqual(self.parte.fotos.count(), 2)
			self.assertContains(respuesta, "no es una imagen")
			self.assertContains(self.client.get(reverse("detalle_parte", args=[self.parte.pk])), "<img")


class RolesTests(TestCase):
	def setUp(self):
		from io import StringIO
		from django.core.management import call_command
		from .models import ParteSiniestro

		call_command("cargar_catalogos", "--demo", stdout=StringIO())
		self.parte = ParteSiniestro.objects.get(estado_parte__nombre_estado="Iniciado")
		self.parte.descripcion_confidencial = "Dato sensible"
		self.parte.save()
		self.sin_rol = get_user_model().objects.create_user("sinrol", password="x")

	def test_se_crean_los_roles_del_srs_y_estadistica(self):
		from django.contrib.auth.models import Group

		self.assertEqual(set(Group.objects.values_list("name", flat=True)), {
			"Bombero", "Personal de guardia", "Estadística", "Jefe", "Graduado", "Aspirante", "Invitado"})

	def test_sin_rol_no_puede_crear_ni_cerrar(self):
		self.client.force_login(self.sin_rol)

		self.assertRedirects(self.client.get(reverse("crear_parte")), reverse("menu"))
		self.client.post(reverse("cerrar_parte", args=[self.parte.pk]))
		self.parte.refresh_from_db()
		self.assertEqual(self.parte.estado_parte.nombre_estado, "Iniciado")

	def test_confidencial_solo_para_el_jefe(self):
		url = reverse("detalle_parte", args=[self.parte.pk])
		self.client.login(username="guardia", password="bomberos2026")
		self.assertNotContains(self.client.get(url), "Dato sensible")
		self.assertNotContains(self.client.get(reverse("completar_parte", args=[self.parte.pk])),
		                       "descripcion_confidencial")

		self.client.login(username="jefe", password="bomberos2026")
		self.assertContains(self.client.get(url), "Dato sensible")
		self.assertContains(self.client.get(reverse("completar_parte", args=[self.parte.pk])),
		                    "descripcion_confidencial")

	def test_guardia_sin_permiso_no_borra_confidencial_al_completar(self):
		from .models import ParteSiniestro

		self.client.login(username="guardia", password="bomberos2026")
		datos = {"unidad_salio": "on", "combustible_nivel": "", "observaciones": "x"}
		for prefijo in ("personal", "rodados", "entidades"):
			datos[f"{prefijo}-TOTAL_FORMS"] = 0
			datos[f"{prefijo}-INITIAL_FORMS"] = 0
		self.client.post(reverse("completar_parte", args=[self.parte.pk]), datos)

		self.assertEqual(ParteSiniestro.objects.get(pk=self.parte.pk).descripcion_confidencial,
		                 "Dato sensible")


class AltaAutomaticaTests(TestCase):
	"""Fecha, hora de llamada y dirección se completan sin que el guardia las tipee."""

	@classmethod
	def setUpTestData(cls):
		from io import StringIO
		from django.core.management import call_command
		from .models import Motivo, TipoSiniestro

		call_command("cargar_catalogos", "--demo", stdout=StringIO())
		cls.tipo = TipoSiniestro.objects.get(descripcion="Incendio")
		cls.motivo = Motivo.objects.get(codigo="101")

	def setUp(self):
		from django.core.cache import cache

		cache.clear()
		self.client.login(username="guardia", password="bomberos2026")

	def alta(self, **extra):
		from .models import ParteSiniestro

		datos = {"direccion_input": "Manuel Belgrano 450", "tipo": self.tipo.pk, "motivo": self.motivo.pk,
		         "caracteristica": "353", "telefono_numero": "4123456", "barrio": "Centro"}
		datos.update(extra)
		respuesta = self.client.post(reverse("crear_parte"), datos)
		parte = ParteSiniestro.objects.latest("pk")
		self.assertRedirects(respuesta, reverse("detalle_parte", args=[parte.pk]))
		return parte

	def test_toma_fecha_y_hora_de_la_pc(self):
		from django.utils import timezone

		momento = timezone.localtime() - timezone.timedelta(minutes=7)
		parte = self.alta(momento_llamada=momento.strftime("%Y-%m-%dT%H:%M:%S"))
		self.assertEqual(parte.fecha, momento.date())
		self.assertEqual(parte.hora_llamada, momento.time().replace(second=0, microsecond=0))

	def test_sin_hora_o_reloj_desfasado_usa_la_del_servidor(self):
		from django.utils import timezone

		parte = self.alta(momento_llamada="2001-01-01T03:00:00")
		self.assertEqual(parte.fecha, timezone.localdate())
		self.assertEqual(self.alta().fecha, timezone.localdate())

	def test_guarda_coordenadas_y_barrio_elegidos(self):
		parte = self.alta(barrio="Centro Sur", latitud="-32.418635", longitud="-63.244709")
		self.assertEqual((parte.ubicacion_calle, parte.ubicacion_numero), ("Manuel Belgrano", "450"))
		self.assertEqual(parte.barrio, "Centro Sur")
		self.assertEqual(str(parte.latitud), "-32.418635")

	def test_busqueda_con_la_casa_en_el_mapa(self):
		from unittest import mock

		casa = [{"lat": "-32.4186352", "lon": "-63.2447094", "address": {
			"road": "Manuel Belgrano", "house_number": "450", "neighbourhood": "Centro Sur"}}]
		with mock.patch("partes.geocodificacion._pedir_json", return_value=casa) as pedido:
			datos = self.client.get(reverse("buscar_direccion"), {"q": "belgrano 450"}).json()
		self.assertEqual(pedido.call_count, 1)  # no hace falta Georef
		self.assertEqual(datos["candidatos"][0], {
			"calle": "Manuel Belgrano", "numero": "450", "barrio": "Centro Sur",
			"latitud": -32.418635, "longitud": -63.244709, "exacta": True})

	def test_busqueda_sin_la_casa_ubica_la_altura_y_pide_el_barrio(self):
		from unittest import mock

		calle = [{"lat": "-32.41", "lon": "-63.24", "address": {"road": "Manuel Belgrano", "neighbourhood": "Centro"}}]
		georef = {"direcciones": [{"ubicacion": {"lat": -32.43, "lon": -63.25}}]}
		inverso = {"address": {"road": "Manuel Belgrano", "suburb": "Barrio Belgrano"}}
		with mock.patch("partes.geocodificacion._pedir_json", side_effect=[calle, georef, inverso]):
			candidato = self.client.get(reverse("buscar_direccion"), {"q": "Belgrano 1450"}).json()["candidatos"][0]
		self.assertEqual((candidato["numero"], candidato["barrio"]), ("1450", "Belgrano"))  # sin el prefijo "Barrio "
		self.assertEqual(candidato["latitud"], -32.43)

	def test_expande_abreviaturas_de_calles(self):
		from .geocodificacion import expandir_abreviaturas

		self.assertEqual(expandir_abreviaturas("Bv. España"), "Bulevar España")
		self.assertEqual(expandir_abreviaturas("gral  paz"), "General paz")
		self.assertEqual(expandir_abreviaturas("Avellaneda"), "Avellaneda")

	def test_calle_tipeada_se_guarda_con_el_nombre_del_listado(self):
		from .geocodificacion import calles

		self.assertIn("Manuel Belgrano", calles())
		self.assertContains(self.client.get(reverse("crear_parte")), '"Manuel Belgrano"')
		parte = self.alta(direccion_input="manuel BELGRANO 450")
		self.assertEqual((parte.ubicacion_calle, parte.ubicacion_numero), ("Manuel Belgrano", "450"))
		self.assertEqual(self.alta(direccion_input="Bv. España 300").ubicacion_calle, "Bulevar España")

	def test_busqueda_de_direccion_exige_sesion(self):
		self.client.logout()
		respuesta = self.client.get(reverse("buscar_direccion"), {"q": "Belgrano 450"})
		self.assertEqual(respuesta.status_code, 302)

	def test_busqueda_sin_conexion_no_rompe(self):
		from unittest import mock

		with mock.patch("partes.geocodificacion._pedir_json", side_effect=OSError("sin red")):
			respuesta = self.client.get(reverse("buscar_direccion"), {"q": "Belgrano 450"})
		self.assertEqual(respuesta.json(), {"candidatos": []})
		self.assertContains(self.client.get(reverse("crear_parte")), 'name="momento_llamada"')


class CompletarComoPlanillaTests(TestCase):
	"""Completar sigue la planilla: grilla de mérito, autoridades, damnificados por Rodado N°, incendio."""

	@classmethod
	def setUpTestData(cls):
		from io import StringIO
		from django.core.management import call_command
		from .models import Integrante, ParteSiniestro, TipoEntidad

		call_command("cargar_catalogos", "--demo", stdout=StringIO())
		cls.parte = ParteSiniestro.objects.exclude(estado_parte__nombre_estado="Cerrado").first()
		cls.bombero = Integrante.objects.get(legajo="1003")
		cls.policia = TipoEntidad.objects.get(descripcion="Policía")
		cls.transito = TipoEntidad.objects.get(descripcion="Tránsito")

	def setUp(self):
		self.client.login(username="jefe", password="bomberos2026")

	def post(self, **extra):
		p = self.parte
		datos = {
			"tipo": p.tipo_id, "motivo": p.motivo_id, "ubicacion_calle": "manuel belgrano",
			"ubicacion_numero": "450", "barrio": "Centro Sur", "unidad_salio": "",
			"rodados-TOTAL_FORMS": 0, "rodados-INITIAL_FORMS": 0,
			"damnificados-TOTAL_FORMS": 0, "damnificados-INITIAL_FORMS": 0,
		}
		# Los rodados y damnificados que ya tenga el parte de demo se reenvían tal cual.
		for i, r in enumerate(p.vehiculos_siniestrados.all()):
			datos.update({f"rodados-{i}-id": r.pk, f"rodados-{i}-rodado_numero": r.rodado_numero})
			datos["rodados-TOTAL_FORMS"] = datos["rodados-INITIAL_FORMS"] = i + 1
		from .models import Damnificado
		for i, d in enumerate(Damnificado.objects.filter(vehiculo_siniestrado__parte=p)
		                      .order_by("vehiculo_siniestrado__rodado_numero", "fecha_captura_local")):
			datos.update({f"damnificados-{i}-id": d.pk, f"damnificados-{i}-rodado_numero": d.vehiculo_siniestrado.rodado_numero,
			              f"damnificados-{i}-apellido_nombre": d.apellido_nombre, f"damnificados-{i}-rol": d.rol,
			              f"damnificados-{i}-triage": d.triage})
			datos["damnificados-TOTAL_FORMS"] = datos["damnificados-INITIAL_FORMS"] = i + 1
		datos.update(extra)
		respuesta = self.client.post(reverse("completar_parte", args=[p.pk]), datos)
		self.assertRedirects(respuesta, reverse("detalle_parte", args=[p.pk]))

	def test_pantalla_carga_con_grilla_y_autoridades(self):
		respuesta = self.client.get(reverse("completar_parte", args=[self.parte.pk]))
		self.assertContains(respuesta, "Personal que concurrió")
		self.assertContains(respuesta, 'name="personal-integrantes"')
		self.assertContains(respuesta, f'name="entidades-entidad_{self.policia.pk}_movil"')
		self.assertContains(respuesta, "Rollo de fardo")
		self.assertContains(respuesta, '<option value="Florentino Ameghino">')

	def test_grilla_de_merito_agrega_y_quita_personal(self):
		self.post(**{"personal-integrantes": [self.bombero.pk]})
		self.assertEqual(list(self.parte.personal.values_list("integrante", flat=True)), [self.bombero.pk])
		self.post()
		self.assertFalse(self.parte.personal.exists())

	def test_autoridades_con_movil_quedan_marcadas(self):
		self.post(**{f"entidades-entidad_{self.policia.pk}_movil": "Móvil 12",
		             f"entidades-entidad_{self.transito.pk}_intervino": "on"})
		entidades = {e.tipo_entidad: e.numero_movil for e in self.parte.entidades.all()}
		self.assertEqual(entidades, {self.policia: "Móvil 12", self.transito: ""})
		self.post()
		self.assertFalse(self.parte.entidades.exists())

	def test_damnificado_con_rodado_nuevo_lo_crea(self):
		self.post(**{"damnificados-TOTAL_FORMS": 1, "damnificados-INITIAL_FORMS": 0,
		             "damnificados-0-rodado_numero": "4", "damnificados-0-apellido_nombre": "Paz, Ana",
		             "damnificados-0-rol": "peaton", "damnificados-0-triage": "verde"})
		rodado = self.parte.vehiculos_siniestrados.get(rodado_numero=4)
		self.assertEqual(rodado.damnificados.get().apellido_nombre, "Paz, Ana")

	def test_incendio_y_ubicacion_normalizada(self):
		self.post(**{"incendio-lo_danado": ["Casa", "Pastizales"], "incendio-damnificado_nombre": "Ruiz, Eva"})
		self.parte.refresh_from_db()
		self.assertEqual(self.parte.incendio.tipo_bien_danado, "Casa, Pastizales")
		self.assertEqual(self.parte.ubicacion_calle, "Manuel Belgrano")


class SinSenalTests(TestCase):
	"""Completar sin señal: varios celulares sobre el mismo parte, reenvíos y fotos."""

	setUp = FlujoParteTests.setUp
	datos_completar = FlujoParteTests.datos_completar

	def estado_inicial(self):
		"""Lo que muestra Completar al abrirlo: es la base que guarda cada celular."""
		self.client.post(reverse("corregir_parte", args=[self.parte.pk]), self.datos_completar())
		return self.datos_completar()

	@staticmethod
	def pares(datos):
		resultado = []
		for nombre, valor in datos.items():
			for v in (valor if isinstance(valor, (list, tuple)) else [valor]):
				resultado.append([nombre, str(v)])
		return resultado

	def sincronizar(self, base, envio=None, **cambios):
		import json
		import uuid

		datos = dict(base)
		datos.update(cambios)
		datos["_base"] = json.dumps(self.pares(base))
		datos["_envio"] = envio or str(uuid.uuid4())
		return self.client.post(reverse("sincronizar_parte", args=[self.parte.pk]), datos)

	def rodado_nuevo(self, numero, marca, uuid_local, damnificado=None):
		datos = {"rodados-TOTAL_FORMS": 1, "rodados-INITIAL_FORMS": 0,
		         "rodados-0-rodado_numero": numero, "rodados-0-marca": marca,
		         "rodados-0-uuid_local": uuid_local}
		if damnificado:
			datos.update({"damnificados-TOTAL_FORMS": 1, "damnificados-INITIAL_FORMS": 0,
			              "damnificados-0-rodado_numero": numero, "damnificados-0-apellido_nombre": damnificado,
			              "damnificados-0-rol": "conductor", "damnificados-0-triage": "verde"})
		return datos

	def test_dos_celulares_suman_personal_y_rodados(self):
		import uuid
		from .forms import PersonalConcurrioForm

		base = self.estado_inicial()
		otro = PersonalConcurrioForm(parte=self.parte).fields["integrantes"].queryset.exclude(pk=self.bombero.pk).first()
		uno, dos = str(uuid.uuid4()), str(uuid.uuid4())

		a = self.sincronizar(base, **{"personal-integrantes": [self.bombero.pk]}, **self.rodado_nuevo(1, "Ford", uno))
		b = self.sincronizar(base, **{"personal-integrantes": [otro.pk]},
		                     **self.rodado_nuevo(1, "Fiat", dos, damnificado="Paz, Ana"))

		self.assertEqual(a.json(), {"ok": True, "avisos": []})
		self.assertEqual(b.status_code, 200)
		self.assertIn("quedó como N° 2", b.json()["avisos"][0])
		self.assertEqual(set(self.parte.personal.values_list("integrante", flat=True)), {self.bombero.pk, otro.pk})
		rodados = {str(r.pk): (r.rodado_numero, r.marca) for r in self.parte.vehiculos_siniestrados.all()}
		self.assertEqual(rodados, {uno: (1, "Ford"), dos: (2, "Fiat")})
		self.assertEqual(self.parte.vehiculos_siniestrados.get(pk=dos).damnificados.get().apellido_nombre, "Paz, Ana")

	def test_dato_suelto_gana_el_ultimo_y_queda_el_aviso(self):
		base = self.estado_inicial()

		self.sincronizar(base, hora_regreso="11:40", observaciones="Humo en la cocina", _modo="corregir")
		respuesta = self.sincronizar(base, hora_regreso="11:50", _modo="corregir")

		self.parte.refresh_from_db()
		self.assertEqual(self.parte.hora_regreso.strftime("%H:%M"), "11:50")
		self.assertEqual(self.parte.observaciones, "Humo en la cocina")  # B no lo tocó: no lo pisa
		self.assertIn("Regreso Hs.", respuesta.json()["avisos"][0])
		self.assertIn("Regreso Hs.", self.parte.envios.exclude(avisos="").get().avisos)
		self.assertContains(self.client.get(reverse("detalle_parte", args=[self.parte.pk])), "se reemplazaron")

	def test_quitar_personal_en_un_celular_no_borra_lo_de_otro(self):
		from .forms import PersonalConcurrioForm

		otro = PersonalConcurrioForm(parte=self.parte).fields["integrantes"].queryset.exclude(pk=self.bombero.pk).first()
		self.client.post(reverse("completar_parte", args=[self.parte.pk]), self.datos_completar(personal=[self.bombero]))
		base = self.datos_completar(personal=[self.bombero])

		self.sincronizar(base, **{"personal-integrantes": [self.bombero.pk, otro.pk]})
		self.sincronizar(base, **{"personal-integrantes": []})

		self.assertEqual(list(self.parte.personal.values_list("integrante", flat=True)), [otro.pk])

	def test_reenviar_no_duplica(self):
		import uuid

		base = self.estado_inicial()
		rodado = str(uuid.uuid4())
		envio = str(uuid.uuid4())

		self.sincronizar(base, envio=envio, **self.rodado_nuevo(1, "Ford", rodado, damnificado="Paz, Ana"))
		repetido = self.sincronizar(base, envio=envio, **self.rodado_nuevo(1, "Ford", rodado, damnificado="Paz, Ana"))
		# Otro guardado del mismo celular (la pantalla sigue mostrando el rodado como nuevo).
		self.sincronizar(base, **self.rodado_nuevo(1, "Ford Ka", rodado))

		self.assertTrue(repetido.json()["ok"])
		self.assertEqual(self.parte.vehiculos_siniestrados.get().marca, "Ford Ka")
		self.assertEqual(self.parte.vehiculos_siniestrados.get().damnificados.count(), 1)

	def test_completar_con_senal_tambien_combina_y_confirma_el_guardado(self):
		import json
		import uuid

		base = self.estado_inicial()
		self.sincronizar(base, observaciones="Desde el celular")
		envio = str(uuid.uuid4())
		datos = dict(base, hora_regreso="12:00", _base=json.dumps(self.pares(base)), _envio=envio, _modo="corregir")

		respuesta = self.client.post(reverse("completar_parte", args=[self.parte.pk]), datos)

		self.assertRedirects(respuesta, reverse("detalle_parte", args=[self.parte.pk]) + f"?guardado={envio}")
		self.parte.refresh_from_db()
		self.assertEqual(self.parte.observaciones, "Desde el celular")
		self.assertEqual(self.parte.hora_regreso.strftime("%H:%M"), "12:00")

	def test_errores_sin_sesion_y_parte_cerrado(self):
		base = self.estado_inicial()

		con_error = self.sincronizar(base, km_regreso=self.movil.kilometraje_actual - 10)
		self.assertEqual(con_error.status_code, 400)
		self.assertIn("Km regreso", " ".join(con_error.json()["errores"]))

		self.client.post(reverse("completar_parte", args=[self.parte.pk]),
		                 self.datos_completar(personal=[self.bombero], km_regreso=self.movil.kilometraje_actual + 5))
		self.client.post(reverse("cerrar_parte", args=[self.parte.pk]), {"clave": "bomberos2026"})
		get_user_model().objects.create_user("bombero2", password="clave-segura-123")
		self.client.login(username="bombero2", password="clave-segura-123")
		self.assertEqual(self.sincronizar(base, observaciones="tarde").status_code, 409)

		self.client.logout()
		self.assertEqual(self.sincronizar(base).status_code, 401)

	def test_descargar_y_cerrar_libera_el_celular(self):
		url = reverse("descargar_parte", args=[self.parte.pk])

		respuesta = self.client.post(url)

		self.assertIn(reverse("completar_parte", args=[self.parte.pk]), respuesta.json()["urls"])
		self.assertContains(self.client.get(reverse("detalle_parte", args=[self.parte.pk])), "Descargado en el celular de")
		self.client.post(reverse("completar_parte", args=[self.parte.pk]),
		                 self.datos_completar(personal=[self.bombero], km_regreso=self.movil.kilometraje_actual + 5,
		                                      _modo="corregir"))  # con la hora de salida
		self.client.post(reverse("cerrar_parte", args=[self.parte.pk]), {"clave": "bomberos2026"})
		self.assertFalse(self.parte.descargas.filter(activa=True).exists())

	def test_foto_sacada_sin_senal_se_sube_una_sola_vez(self):
		import tempfile
		import uuid
		from django.core.files.uploadedfile import SimpleUploadedFile
		from django.test import override_settings

		gif = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00"
		       b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
		foto = str(uuid.uuid4())
		with tempfile.TemporaryDirectory() as carpeta, override_settings(MEDIA_ROOT=carpeta):
			for _ in range(2):
				respuesta = self.client.post(reverse("sincronizar_foto", args=[self.parte.pk]), {
					"id": foto, "fecha_captura_local": "2026-10-16T10:20:00-03:00",
					"archivos": SimpleUploadedFile("hecho.gif", gif, content_type="image/gif")})
				self.assertEqual(respuesta.json(), {"ok": True})

		guardada = self.parte.fotos.get()
		self.assertEqual(str(guardada.pk), foto)
		self.assertEqual(guardada.fecha_captura_local.hour, 13)  # 10:20 en Argentina = 13:20 UTC

	def test_pantallas_del_celular(self):
		self.assertContains(self.client.get(reverse("completar_parte", args=[self.parte.pk])), 'name="_base"')
		sw = self.client.get("/sw.js")
		self.assertEqual(sw["Content-Type"], "application/javascript")
		self.assertContains(sw, reverse("sin_conexion"))
		self.client.logout()
		self.assertEqual(self.client.get(reverse("sin_conexion")).status_code, 200)


class SesionSoloGuardiaTests(TestCase):
	def test_el_bombero_no_tiene_cambio_de_guardia(self):
		from django.contrib.auth.models import Group
		from .roles import BOMBERO
		from .sesion import CLAVE_REGLA, INACTIVIDAD

		user = get_user_model().objects.create_user("bombero", password="clave-segura-123")
		user.groups.add(Group.objects.create(name=BOMBERO))
		self.client.post(reverse("login"), {"username": "bombero", "password": "clave-segura-123"})

		respuesta = self.client.get(reverse("menu"))

		self.assertEqual(self.client.session[CLAVE_REGLA][0], INACTIVIDAD)
		self.assertContains(respuesta, "Tu sesión se cierra tras 12 h sin actividad")
		self.assertNotContains(respuesta, "cambio de guardia")


class ValidacionDeDatosTests(TestCase):
	"""El parte no se crea incompleto y los datos tipeados se controlan (DNI, edad, teléfono, patente)."""

	setUp = FlujoParteTests.setUp
	datos_completar = FlujoParteTests.datos_completar

	def test_alta_sin_altura_barrio_o_telefono_no_crea_el_parte(self):
		from .models import ParteSiniestro

		antes = ParteSiniestro.objects.count()
		respuesta = self.client.post(reverse("crear_parte"), {
			"direccion_input": "Belgrano", "tipo": self.parte.tipo_id, "motivo": self.parte.motivo_id,
			"medio_llamado": "telefonico", "caracteristica": "353", "telefono_numero": "41234"})

		self.assertEqual(respuesta.status_code, 200)
		self.assertEqual(ParteSiniestro.objects.count(), antes)
		self.assertContains(respuesta, "Escribí la calle y la altura")
		self.assertContains(respuesta, "tienen que sumar 10 números")
		self.assertTrue(respuesta.context["form"].errors.get("barrio"))

	def test_damnificado_con_dni_con_puntos_y_patente_en_minusculas(self):
		self.client.post(reverse("completar_parte", args=[self.parte.pk]), self.datos_completar(**{
			"rodados-TOTAL_FORMS": 1, "rodados-0-rodado_numero": "1", "rodados-0-dominio": "ab 123 cd",
			"damnificados-TOTAL_FORMS": 1, "damnificados-0-rodado_numero": "1",
			"damnificados-0-apellido_nombre": "Paz,  Ana", "damnificados-0-dni": "30.123.456",
			"damnificados-0-edad": "40", "damnificados-0-rol": "conductor", "damnificados-0-triage": "verde"}))

		rodado = self.parte.vehiculos_siniestrados.get()
		self.assertEqual(rodado.dominio, "AB123CD")
		self.assertEqual(rodado.damnificados.get().dni, "30123456")
		self.assertEqual(rodado.damnificados.get().apellido_nombre, "Paz, Ana")

	def test_dni_con_letras_edad_imposible_y_patente_invalida_son_error(self):
		respuesta = self.client.post(reverse("completar_parte", args=[self.parte.pk]), self.datos_completar(**{
			"rodados-TOTAL_FORMS": 1, "rodados-0-rodado_numero": "1", "rodados-0-dominio": "XYZ",
			"damnificados-TOTAL_FORMS": 1, "damnificados-0-rodado_numero": "1",
			"damnificados-0-apellido_nombre": "Paz 123", "damnificados-0-dni": "30abc456",
			"damnificados-0-edad": "150", "damnificados-0-rol": "conductor", "damnificados-0-triage": "verde"}))

		self.assertEqual(respuesta.status_code, 200)
		for texto in ("Patente inválida", "7 u 8 números", "entre 0 y 120", "Solo letras"):
			self.assertContains(respuesta, texto)
		self.assertFalse(self.parte.vehiculos_siniestrados.exists())

	def test_completar_no_cambia_la_hora_de_llamada(self):
		self.client.post(reverse("completar_parte", args=[self.parte.pk]), self.datos_completar(hora_llamada="03:00"))

		self.parte.refresh_from_db()
		self.assertEqual(self.parte.hora_llamada.strftime("%H:%M"), "10:00")
		self.assertNotContains(self.client.get(reverse("completar_parte", args=[self.parte.pk])), 'name="hora_llamada"')

	def test_no_quedan_comentarios_de_plantilla_a_la_vista(self):
		respuesta = self.client.get(reverse("completar_parte", args=[self.parte.pk]))

		self.assertNotContains(respuesta, "{#")
		self.assertContains(respuesta, "Ver el detalle del parte")


class CompletarUnaVezTests(TestCase):
	"""El bombero completa una sola vez; las correcciones las hace el guardia."""

	setUp = FlujoParteTests.setUp
	datos_completar = FlujoParteTests.datos_completar

	def test_bombero_completa_una_sola_vez(self):
		get_user_model().objects.create_user("bombero2", password="clave-segura-123")
		self.client.login(username="bombero2", password="clave-segura-123")
		url = reverse("completar_parte", args=[self.parte.pk])

		primero = self.client.post(url, self.datos_completar(observaciones="Primera"))
		segundo = self.client.post(url, self.datos_completar(observaciones="Segunda"))

		self.assertRedirects(primero, reverse("detalle_parte", args=[self.parte.pk]))
		self.assertRedirects(segundo, reverse("detalle_parte", args=[self.parte.pk]))
		self.parte.refresh_from_db()
		self.assertEqual(self.parte.observaciones, "Primera")
		self.assertRedirects(self.client.get(url), reverse("detalle_parte", args=[self.parte.pk]))
		self.assertContains(self.client.get(reverse("detalle_parte", args=[self.parte.pk])), "Ya enviaste tus datos")

	def test_el_guardia_corrige_las_veces_que_haga_falta(self):
		url = reverse("completar_parte", args=[self.parte.pk])
		self.client.post(url, self.datos_completar(observaciones="Primera"))
		self.client.post(url, self.datos_completar(observaciones="Corregida"))

		self.parte.refresh_from_db()
		self.assertEqual(self.parte.observaciones, "Corregida")


class EscalafonYBarrioTests(TestCase):
	def setUp(self):
		from io import StringIO
		from django.core.management import call_command

		call_command("cargar_catalogos", "--demo", stdout=StringIO())

	def test_rangos_agrupados_y_jefe_cabeza_del_cuerpo(self):
		from .models import Integrante, Rango

		self.assertEqual(Rango.objects.get(nombre_rango="Comisario General").escalafon, "oficial")
		self.assertEqual(Rango.objects.get(nombre_rango="Sargento").escalafon, "suboficial")
		self.assertEqual(Rango.objects.get(nombre_rango="Aspirante").escalafon, "bombero")
		jefe = Integrante.objects.get(usuario__username="jefe")
		self.assertTrue(jefe.rango.es_cabeza)
		self.client.login(username="jefe", password="bomberos2026")
		self.assertContains(self.client.get(reverse("mi_perfil")), "Cabeza del cuerpo")

	def test_alta_trae_barrios_de_partes_anteriores(self):
		self.client.login(username="guardia", password="bomberos2026")
		respuesta = self.client.get(reverse("crear_parte"))

		self.assertContains(respuesta, 'id="historial-barrios"')
		self.assertTrue(any(barrio for _, _, barrio in respuesta.context["historial_barrios"]))


class BomberosTests(TestCase):
	"""Sección Bomberos: listado, ficha como RUBA con métricas, alta y edición."""

	@classmethod
	def setUpTestData(cls):
		from io import StringIO
		from django.core.management import call_command
		from .models import Integrante

		call_command("cargar_catalogos", "--demo", stdout=StringIO())
		cls.bombero = Integrante.objects.get(legajo="1003")

	def setUp(self):
		self.client.login(username="estadistica", password="bomberos2026")

	def test_el_guardia_no_ve_las_fichas(self):
		self.client.login(username="guardia", password="bomberos2026")

		self.assertRedirects(self.client.get(reverse("bomberos")), reverse("menu"))

	def test_listado_filtra_y_ficha_muestra_participacion(self):
		self.assertContains(self.client.get(reverse("bomberos"), {"escalafon": "bombero"}), "Rossi")
		self.assertNotContains(self.client.get(reverse("bomberos"), {"escalafon": "oficial"}), "Rossi")
		url = reverse("ficha_bombero", args=[self.bombero.pk])
		self.assertContains(self.client.get(url), "Actividad")
		self.assertContains(self.client.get(url + "?pestana=familia"), "Familiares")
		self.assertContains(self.client.get(url + "?pestana=datos"), "Indefinido")  # donante de sangre como en RUBA

	def test_alta_con_familiar_y_numero_de_merito(self):
		from .models import EstadoIntegrante, Integrante, Rango

		respuesta = self.client.post(reverse("nuevo_bombero"), {
			"legajo": "2001", "apellido": "Luna", "nombre": "Ana", "dni": "41.222.333",
			"rango": Rango.objects.get(nombre_rango="Aspirante").pk,
			"estado": EstadoIntegrante.objects.get(nombre_estado="Activo").pk,
			"donante_sangre": "si", "numero_merito": "250", "fecha_aspirante": "2026-03-01",
			"familiares-TOTAL_FORMS": 1, "familiares-INITIAL_FORMS": 0,
			"familiares-0-vinculo": "Madre", "familiares-0-apellido": "Luna", "familiares-0-nombre": "Rosa",
			"familiares-0-vive": "on",
			"historial-TOTAL_FORMS": 0, "historial-INITIAL_FORMS": 0,
		})

		nueva = Integrante.objects.get(legajo="2001")
		self.assertRedirects(respuesta, reverse("ficha_bombero", args=[nueva.pk]))
		self.assertEqual(nueva.dni, "41222333")
		self.assertEqual(nueva.familiares.get().nombre, "Rosa")
		self.assertEqual(nueva.numero_merito_en(nueva.numeros_merito.get().anio), 250)

	def test_numero_de_merito_repetido_es_error(self):
		from .models import NumeroMerito

		otro = NumeroMerito.objects.exclude(integrante=self.bombero).first()
		datos = {campo: getattr(self.bombero, campo) for campo in ("legajo", "apellido", "nombre", "dni")}
		datos.update({"rango": self.bombero.rango_id, "estado": self.bombero.estado_id, "donante_sangre": "no",
		              "numero_merito": otro.numero_merito,
		              "familiares-TOTAL_FORMS": 0, "familiares-INITIAL_FORMS": 0,
		              "historial-TOTAL_FORMS": 0, "historial-INITIAL_FORMS": 0})

		respuesta = self.client.post(reverse("editar_bombero", args=[self.bombero.pk]), datos)

		self.assertContains(respuesta, "ya lo tiene otro integrante")


class PedidosDelTutorTests(TestCase):
	"""Ajustes del 17/10: llamado por radio o en persona, motivos y secciones por tipo,
	horarios del lado del guardia."""

	setUp = FlujoParteTests.setUp
	datos_completar = FlujoParteTests.datos_completar

	def alta(self, **extra):
		from .models import Motivo, TipoSiniestro
		datos = {"direccion_input": "Sarmiento 200", "barrio": "Centro",
		         "tipo": TipoSiniestro.objects.get(descripcion="Incendio").pk,
		         "motivo": Motivo.objects.get(codigo="101").pk}
		datos.update(extra)
		return self.client.post(reverse("crear_parte"), datos)

	def test_llamado_por_radio_no_pide_telefono(self):
		from .models import ParteSiniestro

		self.alta(medio_llamado="radio", caracteristica="353", telefono_numero="4123456")

		parte = ParteSiniestro.objects.get(ubicacion_calle="Sarmiento")
		self.assertEqual(parte.medio_llamado, "radio")
		self.assertEqual(parte.telefono_origen, "")

	def test_llamado_telefonico_sin_numero_no_crea_el_parte(self):
		respuesta = self.alta(medio_llamado="telefonico")

		self.assertContains(respuesta, "anotá el número")

	def test_motivo_de_otro_tipo_se_rechaza(self):
		from .models import Motivo

		respuesta = self.alta(medio_llamado="en_persona", motivo=Motivo.objects.get(codigo="111").pk)

		self.assertContains(respuesta, "no corresponde a Incendio")

	def test_catalogos_relacionan_tipos_sin_pisar_el_admin(self):
		from io import StringIO
		from django.core.management import call_command
		from .models import Motivo, TipoSiniestro

		accidente = TipoSiniestro.objects.get(descripcion="Accidentes")
		self.assertEqual(accidente.lista_secciones(), ["rodados", "damnificados"])
		self.assertIn(accidente, Motivo.objects.get(codigo="111").tipos.all())
		self.assertTrue(Motivo.objects.create(descripcion="Sin tipos").vale_para(accidente))
		accidente.secciones = "rodados"
		accidente.save()
		call_command("cargar_catalogos", stdout=StringIO())
		accidente.refresh_from_db()
		self.assertEqual(accidente.secciones, "rodados")

	def test_bombero_no_ve_ni_cambia_los_horarios(self):
		get_user_model().objects.create_user("bombero2", password="clave-segura-123")
		self.client.login(username="bombero2", password="clave-segura-123")
		url = reverse("completar_parte", args=[self.parte.pk])

		pantalla = self.client.get(url)
		self.client.post(url, self.datos_completar(hora_salida="23:00", observaciones="Desde el lugar"))

		self.assertNotContains(pantalla, 'name="hora_salida"')
		self.assertContains(pantalla, "los carga el Personal de guardia")
		self.parte.refresh_from_db()
		self.assertIsNone(self.parte.hora_salida)
		self.assertEqual(self.parte.telefono_origen, "353 4123456")
		self.assertEqual(self.parte.observaciones, "Desde el lugar")

	def test_guardia_carga_horarios_y_la_pantalla_trae_secciones_y_buscador(self):
		url = reverse("corregir_parte", args=[self.parte.pk])
		self.assertNotContains(self.client.get(reverse("completar_parte", args=[self.parte.pk])), 'name="hora_salida"')

		pantalla = self.client.get(url)
		self.client.post(url, self.datos_completar(medio_llamado="telefonico", caracteristica="353",
		                                           telefono_numero="4123456"))

		self.assertContains(pantalla, 'name="hora_salida"')
		self.assertContains(pantalla, 'data-seccion="incendio"')
		self.assertContains(pantalla, 'id="buscar-personal"')
		self.assertContains(pantalla, 'id="datos-tipos"')
		self.parte.refresh_from_db()
		self.assertEqual(str(self.parte.hora_salida), "10:03:00")

	def test_damnificado_sin_rodado(self):
		url = reverse("completar_parte", args=[self.parte.pk])
		self.client.post(url, self.datos_completar(**{
			"damnificados-TOTAL_FORMS": 1, "damnificados-0-rodado_numero": "",
			"damnificados-0-apellido_nombre": "Ruiz, Eva", "damnificados-0-triage": "amarillo"}))

		damnificado = self.parte.damnificados.get()
		self.assertIsNone(damnificado.vehiculo_siniestrado)
		self.assertFalse(self.parte.vehiculos_siniestrados.exists())
		self.assertContains(self.client.get(reverse("detalle_parte", args=[self.parte.pk])), "Damnificados sin rodado")

	def test_cuatro_lugares_para_rodados_y_hasta_diez(self):
		url = reverse("completar_parte", args=[self.parte.pk])
		self.assertEqual(self.client.get(url).context["rodados"].total_form_count(), 4)

		datos = {"rodados-TOTAL_FORMS": 5}
		for i in range(5):
			datos.update({f"rodados-{i}-rodado_numero": str(i + 6), f"rodados-{i}-marca": "Fiat"})
		self.client.post(url, self.datos_completar(**datos))

		self.assertEqual(sorted(self.parte.vehiculos_siniestrados.values_list("rodado_numero", flat=True)),
		                 [6, 7, 8, 9, 10])

	def test_bombero_no_cambia_unidad_ni_combustible(self):
		get_user_model().objects.create_user("bombero2", password="clave-segura-123")
		self.client.login(username="bombero2", password="clave-segura-123")

		self.client.post(reverse("completar_parte", args=[self.parte.pk]),
		                 self.datos_completar(km_regreso="999999", combustible_nivel="lleno"))

		self.parte.refresh_from_db()
		self.assertIsNone(self.parte.vehiculo)
		self.assertIsNone(self.parte.km_regreso)
		self.assertEqual(self.parte.combustible_nivel, "")

	def test_merito_de_001_a_999_con_tres_digitos(self):
		from .models import NumeroMerito

		NumeroMerito.objects.filter(integrante=self.bombero, anio=self.parte.anio).update(numero_merito=7)

		pantalla = self.client.get(reverse("completar_parte", args=[self.parte.pk]))

		self.assertContains(pantalla, ">007<small>")


class CapacitacionesTests(TestCase):
	"""Control de Capacitación: alta con asistentes, numeración por año, permisos y ficha del bombero."""

	def setUp(self):
		from .models import Capacitacion
		FlujoParteTests.setUp(self)
		Capacitacion.objects.all().delete()  # la demo trae una de ejemplo
		self.client.login(username="estadistica", password="bomberos2026")

	def datos(self, **extra):
		datos = {"datos": "Uso de ERA", "fecha": "2026-05-10", "hora_inicio": "09:00", "hora_fin": "12:30",
		         "modalidad": "interna", "sentido": "dictada", "nivel": "cuartel",
		         "lista_destinos": ["aspirantes_1"], "de_departamento": "on", "departamentos": ["era"],
		         "asistentes": [self.bombero.pk]}
		datos.update(extra)
		return datos

	def test_alta_con_asistentes_numero_y_kilometraje(self):
		from .models import Capacitacion

		guardia = get_user_model().objects.get(username="guardia").integrante
		respuesta = self.client.post(reverse("nueva_capacitacion"), self.datos(
			unidad="on", vehiculo=self.movil.pk, km_regreso=self.movil.kilometraje_actual + 30, guardia=guardia.pk))
		otra = self.client.post(reverse("nueva_capacitacion"), self.datos(datos="Nudos"))

		primera = Capacitacion.objects.get(tema="Uso de ERA")
		self.assertRedirects(respuesta, reverse("detalle_capacitacion", args=[primera.pk]))
		self.assertEqual((primera.numero_correlativo, primera.anio), (1, 2026))
		self.assertEqual(Capacitacion.objects.get(tema="Nudos").numero_correlativo, 2)
		self.assertEqual(list(primera.asistentes.all()), [self.bombero])
		self.assertEqual(primera.horas, 3.5)
		self.assertEqual(primera.lista_destinos(), ["Aspirantes 1er año", "Dto. ERA"])
		self.assertEqual(primera.guardia, guardia)
		self.assertEqual(primera.cargado_por.username, "estadistica")
		antes = self.movil.kilometraje_actual
		self.movil.refresh_from_db()
		self.assertEqual(self.movil.kilometraje_actual, antes + 30)
		self.assertEqual(otra.status_code, 302)

	def test_corregir_quita_y_suma_asistentes(self):
		from .models import Capacitacion, Integrante

		self.client.post(reverse("nueva_capacitacion"), self.datos())
		capacitacion = Capacitacion.objects.get()
		otro = Integrante.objects.exclude(pk=self.bombero.pk).filter(estado__nombre_estado="Activo").first()

		self.client.post(reverse("editar_capacitacion", args=[capacitacion.pk]), self.datos(asistentes=[otro.pk]))

		self.assertEqual(list(capacitacion.asistentes.all()), [otro])

	def test_aparece_en_la_ficha_del_bombero(self):
		from django.utils import timezone
		from .models import Capacitacion

		self.client.post(reverse("nueva_capacitacion"), self.datos(fecha=timezone.localdate().isoformat()))

		ficha = self.client.get(reverse("ficha_bombero", args=[self.bombero.pk]))

		self.assertContains(ficha, "Uso de ERA")
		self.assertEqual(ficha.context["resumen"]["horas_capacitacion"], 3.5)
		self.assertEqual(ficha.context["resumen"]["capacitaciones"], 1)
		self.assertContains(ficha, reverse("detalle_capacitacion", args=[Capacitacion.objects.get().pk]))

	def test_sin_permiso_no_carga_capacitaciones(self):
		get_user_model().objects.create_user("bombero2", password="clave-segura-123")
		self.client.login(username="bombero2", password="clave-segura-123")

		respuesta = self.client.post(reverse("nueva_capacitacion"), self.datos())

		self.assertRedirects(respuesta, reverse("menu"))
		self.assertRedirects(self.client.get(reverse("capacitaciones")), reverse("menu"))

	def test_pantallas_cargan(self):
		self.client.post(reverse("nueva_capacitacion"), self.datos())
		from .models import Capacitacion
		c = Capacitacion.objects.get()

		for url in [reverse("capacitaciones") + "?anio=2026", reverse("nueva_capacitacion"),
		            reverse("detalle_capacitacion", args=[c.pk]), reverse("editar_capacitacion", args=[c.pk])]:
			self.assertEqual(self.client.get(url).status_code, 200, url)
		self.assertContains(self.client.get(reverse("capacitaciones") + "?anio=2026"), "Uso de ERA")


class PeriodosTests(TestCase):
	"""Tomos anuales: números de mérito a mano, bajas, cierre con resumen congelado y año de solo lectura."""

	@classmethod
	def setUpTestData(cls):
		from io import StringIO
		from django.core.management import call_command
		from django.utils import timezone
		from .models import Integrante, ParteSiniestro, Periodo

		call_command("cargar_catalogos", "--demo", stdout=StringIO())
		cls.anio = timezone.localdate().year
		cls.periodo = Periodo.del_anio(cls.anio)
		cls.bombero = Integrante.objects.get(legajo="1003")
		cls.abierto = ParteSiniestro.objects.exclude(estado_parte__nombre_estado="Cerrado").first()

	def setUp(self):
		self.client.login(username="estadistica", password="bomberos2026")

	def cerrar_partes(self):
		from .models import EstadoParte, ParteSiniestro
		ParteSiniestro.objects.update(estado_parte=EstadoParte.objects.get(nombre_estado="Cerrado"))

	def numeros(self, **cambios):
		form = self.client.get(reverse("numeros_tomo", args=[self.anio])).context["form"]
		datos = {k: v for k, v in ((c, form[c].value()) for c in form.fields) if v not in (None, False)}
		datos.update(cambios)
		return self.client.post(reverse("numeros_tomo", args=[self.anio]), datos)

	def test_estante_y_tomo_abierto(self):
		estante = self.client.get(reverse("tomos"))
		tomo = self.client.get(reverse("tomo", args=[self.anio]))

		self.assertContains(estante, f'href="{reverse("tomo", args=[self.anio])}"')
		self.assertContains(tomo, "Rossi, Mateo")
		self.assertContains(tomo, "Abierto.")
		for ver in ("partes", "capacitaciones"):
			self.assertEqual(self.client.get(reverse("tomo", args=[self.anio]) + f"?ver={ver}").status_code, 200)

	def test_numeros_a_mano_y_repetidos(self):
		repetido = self.numeros(**{f"numero_{self.bombero.pk}": "115"})
		self.assertContains(repetido, "ya se lo diste")

		self.numeros(**{f"numero_{self.bombero.pk}": "7"})
		self.assertEqual(self.bombero.numero_merito_en(self.anio), 7)

	def test_no_cierra_con_partes_abiertos(self):
		self.client.post(reverse("cerrar_tomo", args=[self.anio]), {"clave": "bomberos2026"})

		self.periodo.refresh_from_db()
		self.assertFalse(self.periodo.cerrado)

	def test_cerrar_congela_resumen_y_deja_el_anio_de_solo_lectura(self):
		from .models import Capacitacion
		self.cerrar_partes()

		self.client.post(reverse("cerrar_tomo", args=[self.anio]), {"clave": "bomberos2026"})

		self.periodo.refresh_from_db()
		self.assertTrue(self.periodo.cerrado)
		resumen = self.periodo.resumenes.get(integrante=self.bombero)
		self.assertEqual(resumen.numero_merito, 142)
		self.assertGreaterEqual(resumen.salidas, 1)
		# Cambiar el número después no toca el tomo cerrado.
		self.bombero.numeros_merito.filter(anio=self.anio).update(numero_merito=999)
		self.assertContains(self.client.get(reverse("tomo", args=[self.anio])), ">142<")
		# Nada del año se modifica.
		self.client.login(username="guardia", password="bomberos2026")
		self.assertRedirects(self.client.get(reverse("crear_parte")), reverse("menu"))
		self.assertRedirects(self.client.get(reverse("completar_parte", args=[self.abierto.pk])),
		                     reverse("detalle_parte", args=[self.abierto.pk]))
		self.client.login(username="estadistica", password="bomberos2026")
		self.client.post(reverse("nueva_capacitacion"), {"datos": "Nudos", "fecha": f"{self.anio}-03-01",
		                                                 "modalidad": "interna", "sentido": "dictada", "nivel": "cuartel"})
		self.assertFalse(Capacitacion.objects.filter(tema="Nudos").exists())

	def test_abrir_el_siguiente_propone_los_numeros_anteriores(self):
		from .models import Periodo
		self.cerrar_partes()
		self.client.post(reverse("cerrar_tomo", args=[self.anio]), {"clave": "bomberos2026"})

		respuesta = self.client.post(reverse("abrir_tomo"))

		self.assertRedirects(respuesta, reverse("numeros_tomo", args=[self.anio + 1]))
		self.assertFalse(Periodo.objects.get(anio=self.anio + 1).cerrado)
		form = self.client.get(respuesta.url).context["form"]
		self.assertEqual(form[f"numero_{self.bombero.pk}"].value(), 142)

	def test_planilla_para_estadistica(self):
		respuesta = self.client.get(reverse("tomo", args=[self.anio]) + "?formato=csv")

		contenido = respuesta.content.decode("utf-8-sig")
		self.assertIn("Siniestros asistidos", contenido)
		self.assertIn("142;Rossi;Mateo", contenido)

	def test_permisos(self):
		self.client.login(username="guardia", password="bomberos2026")  # ve los tomos pero no los cierra
		self.assertEqual(self.client.get(reverse("tomos")).status_code, 200)
		self.cerrar_partes()
		self.client.post(reverse("cerrar_tomo", args=[self.anio]), {"clave": "bomberos2026"})
		self.periodo.refresh_from_db()
		self.assertFalse(self.periodo.cerrado)

		self.client.force_login(get_user_model().objects.create_user("sinrol", password="x"))
		self.assertRedirects(self.client.get(reverse("tomos")), reverse("menu"))


class MantenimientoTests(TestCase):
	"""Control de Mantenimiento: alta con personal, costos, km del móvil, ficha y tomo."""

	def setUp(self):
		from .models import Mantenimiento
		FlujoParteTests.setUp(self)
		Mantenimiento.objects.all().delete()  # la demo trae uno de ejemplo
		self.client.login(username="estadistica", password="bomberos2026")

	def datos(self, **extra):
		datos = {"vehiculo": self.movil.pk, "fecha": "2026-06-02", "hora_inicio": "18:00", "hora_fin": "20:30",
		         "tipo": "correctivo", "motivo": "Pérdida en la bomba", "lugar": "cuartel",
		         "costo_repuestos": "15000", "horas_taller": "2", "costo_hora": "5000",
		         "participantes": [self.bombero.pk]}
		datos.update(extra)
		return datos

	def test_alta_con_personal_y_costos(self):
		from .models import Mantenimiento

		respuesta = self.client.post(reverse("nuevo_mantenimiento"), self.datos())

		m = Mantenimiento.objects.get()
		self.assertRedirects(respuesta, reverse("detalle_mantenimiento", args=[m.pk]))
		self.assertEqual((m.numero_correlativo, m.anio), (1, 2026))
		self.assertEqual(list(m.participantes.all()), [self.bombero])
		self.assertEqual(m.horas, 2.5)
		self.assertEqual(m.costo_total, 25000)

	def test_aparece_en_ficha_tomo_y_planilla(self):
		from django.utils import timezone
		hoy = timezone.localdate()
		self.client.post(reverse("nuevo_mantenimiento"), self.datos(fecha=hoy.isoformat()))

		ficha = self.client.get(reverse("ficha_bombero", args=[self.bombero.pk]))
		tomo = self.client.get(reverse("tomo", args=[hoy.year]) + "?ver=mantenimientos")
		planilla = self.client.get(reverse("tomo", args=[hoy.year]) + "?formato=csv").content.decode("utf-8-sig")

		self.assertContains(ficha, "Pérdida en la bomba")
		self.assertContains(tomo, "Pérdida en la bomba")
		fila = next(l for l in planilla.splitlines() if ";Rossi;" in l)
		self.assertTrue(fila.endswith(";1;2,5"), fila)

	def test_pantallas_y_permisos(self):
		from .models import Mantenimiento
		self.client.post(reverse("nuevo_mantenimiento"), self.datos())
		m = Mantenimiento.objects.get()
		for url in [reverse("mantenimientos") + "?anio=2026", reverse("mantenimientos") + f"?anio=2026&movil={self.movil.pk}",
		            reverse("nuevo_mantenimiento"), reverse("detalle_mantenimiento", args=[m.pk]),
		            reverse("editar_mantenimiento", args=[m.pk])]:
			self.assertEqual(self.client.get(url).status_code, 200, url)

		get_user_model().objects.create_user("bombero2", password="clave-segura-123")
		self.client.login(username="bombero2", password="clave-segura-123")
		self.assertRedirects(self.client.get(reverse("mantenimientos")), reverse("menu"))


class PulidoTests(TestCase):
	"""Revisión visual del 17/10: totales del tomo, ficha por año y secciones del detalle según el tipo."""

	setUp = PeriodosTests.setUp

	@classmethod
	def setUpTestData(cls):
		PeriodosTests.setUpTestData.__func__(cls)  # los datos tienen que quedar en esta clase

	def test_tomo_con_totales(self):
		tomo = self.client.get(reverse("tomo", args=[self.anio]))

		filas = tomo.context["filas"]
		self.assertEqual(tomo.context["totales"]["salidas"], sum(f["salidas"] for f in filas))
		self.assertContains(tomo, f"Total {self.anio}")

	def test_ficha_por_anio(self):
		ficha = self.client.get(reverse("ficha_bombero", args=[self.bombero.pk]) + f"?anio={self.anio - 1}")

		self.assertEqual(ficha.context["anio"], self.anio - 1)
		self.assertEqual(ficha.context["resumen"]["salidas"], 0)
		self.assertContains(self.client.get(reverse("ficha_bombero", args=[self.bombero.pk])), "siniestros")

	def test_detalle_muestra_las_secciones_del_tipo(self):
		from .models import ParteSiniestro
		self.client.login(username="jefe", password="bomberos2026")
		incendio = ParteSiniestro.objects.get(tipo__descripcion="Incendio")
		accidente = ParteSiniestro.objects.get(tipo__descripcion="Accidentes")

		self.assertContains(self.client.get(reverse("detalle_parte", args=[incendio.pk])), "Incendio - Datos damnificado")
		self.assertNotContains(self.client.get(reverse("detalle_parte", args=[incendio.pk])), "vehículos siniestrados")
		self.assertNotContains(self.client.get(reverse("detalle_parte", args=[accidente.pk])), "Incendio - Datos damnificado")

	def test_motivo_viejo_de_otro_tipo_no_se_pierde_al_abrir_completar(self):
		"""El filtro de motivos se hace en el navegador; el servidor sigue mostrando el motivo guardado."""
		from .models import Motivo
		self.client.login(username="jefe", password="bomberos2026")
		colision = Motivo.objects.get(codigo="111")
		type(self.abierto).objects.filter(pk=self.abierto.pk).update(motivo=colision)

		pantalla = self.client.get(reverse("completar_parte", args=[self.abierto.pk]))

		self.assertEqual(pantalla.context["form"]["motivo"].value(), colision.pk)
		self.assertContains(pantalla, "motivoGuardado")


class RolesDelTutorTests(TestCase):
	"""Decisión del 17/10: el guardia solo crea, revisa y cierra partes; agregar y administrar es de Estadística."""

	setUp = PeriodosTests.setUp

	@classmethod
	def setUpTestData(cls):
		PeriodosTests.setUpTestData.__func__(cls)

	def test_el_guardia_trabaja_con_todos_los_partes(self):
		# 28/10 (tutor): el guardia carga partes de siniestro, capacitaciones y mantenimientos; los bomberos, no.
		self.client.login(username="guardia", password="bomberos2026")

		for vista in ("bomberos", "nuevo_bombero"):
			self.assertRedirects(self.client.get(reverse(vista)), reverse("menu"), msg_prefix=vista)
		for vista in ("capacitaciones", "nueva_capacitacion", "mantenimientos", "nuevo_mantenimiento"):
			self.assertEqual(self.client.get(reverse(vista)).status_code, 200, msg=vista)
		self.assertEqual(self.client.get(reverse("tomos")).status_code, 200)  # tomos y PDF: guardia, Estadística y admin
		menu = self.client.get(reverse("menu"))
		self.assertContains(menu, reverse("listado_partes"))
		self.assertContains(menu, reverse("capacitaciones"))
		self.assertContains(menu, reverse("mantenimientos"))

	def test_estadistica_agrega_y_administra(self):
		for vista in ("nueva_capacitacion", "nuevo_mantenimiento", "nuevo_bombero"):
			self.assertEqual(self.client.get(reverse(vista)).status_code, 200, vista)



class FirmasTests(TestCase):
	"""Pedido del 17/10: cerrar un parte, dar de baja y cerrar un período se firman con la contraseña
	y quedan sellados (inmutables y encadenados)."""

	setUp = PeriodosTests.setUp
	cerrar_partes = PeriodosTests.cerrar_partes

	@classmethod
	def setUpTestData(cls):
		PeriodosTests.setUpTestData.__func__(cls)

	def preparar_para_cerrar(self, parte):
		from .models import PartePersonal
		from datetime import time
		PartePersonal.objects.get_or_create(parte=parte, integrante=self.bombero)
		type(parte).objects.filter(pk=parte.pk).update(unidad_salio=False)
		if parte.hora_salida and not parte.hora_regreso:
			type(parte).objects.filter(pk=parte.pk).update(hora_regreso=time(23, 59))

	def test_cerrar_parte_pide_contrasena_y_deja_sello(self):
		from .models import Firma
		self.client.login(username="guardia", password="bomberos2026")
		self.preparar_para_cerrar(self.abierto)
		url = reverse("cerrar_parte", args=[self.abierto.pk])

		self.client.post(url, {"clave": "equivocada"})
		self.abierto.refresh_from_db()
		self.assertFalse(self.abierto.esta_cerrado)

		self.client.post(url, {"clave": "bomberos2026"})
		self.abierto.refresh_from_db()
		self.assertTrue(self.abierto.esta_cerrado)
		firma = Firma.de(self.abierto, Firma.Accion.CIERRE_PARTE).get()
		self.assertIn("Gómez, Lucía", firma.firmante)
		self.assertEqual(firma.datos["parte"], f"{self.abierto.numero_correlativo}/{self.abierto.anio}")
		self.assertContains(self.client.get(reverse("detalle_parte", args=[self.abierto.pk])), firma.codigo)

	def test_la_firma_no_se_modifica_ni_se_borra_y_se_detecta_si_la_alteran(self):
		from .models import Firma, FirmaInmutable
		self.client.login(username="guardia", password="bomberos2026")
		self.preparar_para_cerrar(self.abierto)
		self.client.post(reverse("cerrar_parte", args=[self.abierto.pk]), {"clave": "bomberos2026"})
		firma = Firma.objects.get()

		with self.assertRaises(FirmaInmutable):
			firma.save()
		with self.assertRaises(FirmaInmutable):
			firma.delete()
		self.assertEqual(Firma.verificar_cadena(), [])
		Firma.objects.filter(pk=firma.pk).update(firmante="Otro")
		self.assertEqual(Firma.verificar_cadena(), [firma])

	def test_baja_firmada_desde_la_ficha(self):
		from .models import Firma
		url = reverse("dar_de_baja", args=[self.bombero.pk])

		self.client.post(url, {"motivo": "Renuncia", "clave": "mal"})
		self.bombero.refresh_from_db()
		self.assertEqual(str(self.bombero.estado), "Activo")

		self.client.post(url, {"motivo": "Renuncia", "clave": "bomberos2026"})
		self.bombero.refresh_from_db()
		self.assertEqual(str(self.bombero.estado), "Baja")
		self.assertIsNone(self.bombero.numero_merito_en(self.anio))
		self.assertTrue(self.bombero.historial.filter(motivo_baja="Renuncia").exists())
		firma = Firma.de(self.bombero, Firma.Accion.BAJA_INTEGRANTE).get()
		self.assertEqual(firma.datos["numero_merito"], 142)
		self.assertIn("Bustos, Pablo", firma.firmante)
		trayectoria = self.client.get(reverse("ficha_bombero", args=[self.bombero.pk]) + "?pestana=trayectoria")
		self.assertContains(trayectoria, firma.codigo)
		self.assertNotIn(self.bombero, [f["integrante"] for f in
		                                self.client.get(reverse("numeros_tomo", args=[self.anio])).context["form"].filas])

	def test_el_guardia_no_da_bajas(self):
		self.client.login(username="guardia", password="bomberos2026")
		self.client.post(reverse("dar_de_baja", args=[self.bombero.pk]), {"motivo": "x", "clave": "bomberos2026"})
		self.bombero.refresh_from_db()
		self.assertEqual(str(self.bombero.estado), "Activo")

	def test_cierre_de_periodo_firmado(self):
		from .models import Firma
		self.cerrar_partes()

		self.client.post(reverse("cerrar_tomo", args=[self.anio]), {"clave": "mal"})
		self.periodo.refresh_from_db()
		self.assertFalse(self.periodo.cerrado)

		self.client.post(reverse("cerrar_tomo", args=[self.anio]), {"clave": "bomberos2026"})
		self.periodo.refresh_from_db()
		self.assertTrue(self.periodo.cerrado)
		firma = Firma.de(self.periodo, Firma.Accion.CIERRE_PERIODO).get()
		self.assertTrue(any(b["integrante"].startswith("Rossi, Mateo") for b in firma.datos["bomberos"]))


class NavegacionYSalidaTests(TestCase):
	"""Pedidos del 17/10: menú lateral sin duplicados, inicio con saludo, perfil propio, llamado por radio
	y la métrica de salida con los botones de un toque."""

	setUp = PeriodosTests.setUp

	@classmethod
	def setUpTestData(cls):
		PeriodosTests.setUpTestData.__func__(cls)

	def test_inicio_saluda_y_nuevo_parte_vive_en_partes(self):
		self.client.login(username="guardia", password="bomberos2026")
		inicio = self.client.get(reverse("menu"))

		self.assertContains(inicio, "Hola, Lucía")
		self.assertNotContains(inicio, f'href="{reverse("crear_parte")}"')
		self.assertContains(self.client.get(reverse("listado_partes")), f'href="{reverse("crear_parte")}"')

	def test_mi_perfil_es_el_mismo_perfil_y_cada_uno_ve_el_suyo(self):
		self.client.login(username="guardia", password="bomberos2026")
		propio = self.client.get(reverse("mi_perfil"))
		ajeno = self.client.get(reverse("ficha_bombero", args=[self.bombero.pk]))

		self.assertTemplateUsed(propio, "partes/ficha_bombero.html")
		self.assertContains(propio, "Lucía Gómez")
		self.assertRedirects(ajeno, reverse("menu"))  # el guardia no ve las fichas de los demás

	def test_llamado_por_radio_por_defecto(self):
		from .models import ParteSiniestro
		self.client.login(username="guardia", password="bomberos2026")
		self.assertEqual(self.client.get(reverse("crear_parte")).context["form"]["medio_llamado"].value(), "radio")
		self.assertEqual(ParteSiniestro._meta.get_field("medio_llamado").default, "radio")

	def test_marcar_salida_y_tiempo_hasta_la_salida(self):
		from datetime import time
		from .models import ParteSiniestro
		self.client.login(username="guardia", password="bomberos2026")
		ParteSiniestro.objects.filter(pk=self.abierto.pk).update(hora_llamada=time(0, 0), hora_salida=None)

		self.client.post(reverse("marcar_hora", args=[self.abierto.pk, "salida"]))
		self.abierto.refresh_from_db()
		self.assertIsNotNone(self.abierto.hora_salida)
		self.assertIsNotNone(self.abierto.minutos_despacho)
		primera = self.abierto.hora_salida
		self.client.post(reverse("marcar_hora", args=[self.abierto.pk, "salida"]))  # no pisa
		self.abierto.refresh_from_db()
		self.assertEqual(self.abierto.hora_salida, primera)
		self.client.login(username="estadistica", password="bomberos2026")
		tomo = self.client.get(reverse("tomo", args=[self.anio]) + "?ver=partes")
		self.assertIsNotNone(tomo.context["tiempos_salida"])

	def test_un_bombero_no_marca_horarios(self):
		type(self.abierto).objects.filter(pk=self.abierto.pk).update(hora_llegada=None)
		get_user_model().objects.create_user("bombero2", password="clave-segura-123")
		self.client.login(username="bombero2", password="clave-segura-123")
		self.client.post(reverse("marcar_hora", args=[self.abierto.pk, "llegada"]))
		self.abierto.refresh_from_db()
		self.assertIsNone(self.abierto.hora_llegada)


class MotivoViejoTests(TestCase):
	"""Un parte cargado antes de "motivos por tipo" se sigue pudiendo guardar; cambiar a un motivo
	de otro tipo, no. (Bug del 17/10: Guardar fallaba y el aviso no se veía.)"""

	setUp = FlujoParteTests.setUp
	datos_completar = FlujoParteTests.datos_completar

	def test_combinacion_vieja_se_guarda_y_una_nueva_no(self):
		from .models import Motivo, ParteSiniestro
		colision = Motivo.objects.get(codigo="111")
		ParteSiniestro.objects.filter(pk=self.parte.pk).update(motivo=colision)  # dato viejo: Incendio + Colisión
		self.parte.refresh_from_db()
		url = reverse("completar_parte", args=[self.parte.pk])

		guardado = self.client.post(url, self.datos_completar(observaciones="Sigue igual"))
		self.assertRedirects(guardado, reverse("detalle_parte", args=[self.parte.pk]))

		vuelco = Motivo.objects.get(codigo="118")
		rechazado = self.client.post(url, self.datos_completar(motivo=vuelco.pk))
		self.assertContains(rechazado, "No se guardó. Revisá: Motivo")
		self.parte.refresh_from_db()
		self.assertEqual(self.parte.motivo, colision)

class ImportarListadoTests(TestCase):
	"""Lista de orden operativa del cuartel: tres bloques de columnas, títulos de sección y N° como fórmulas."""

	def setUp(self):
		from io import StringIO
		from django.core.management import call_command

		call_command("cargar_catalogos", stdout=StringIO())

	def planilla(self, carpeta, repetido=False):
		import openpyxl

		libro = openpyxl.Workbook()
		hoja = libro.active
		hoja.append(["Cuerpo de Bomberos Voluntarios de Villa María"])
		hoja.append(["N°", "G", "APELLIDO Y NOMBRE", "P/A", "Fecha:"])
		hoja.append(["OFICIALES SUPERIORES", None, None, None, "Motivo:"])
		hoja.append([101, None, "NICOLA Gustavo 1° Jefe Crio Gral.", None, "ASPIRANTES 2 do Año"])
		hoja.append(["SUB OFICIALES SUPERIORES", None, None, None, 318, 3, "Acuña, Emanuel", None, "RETIRO EFECTIVO"])
		hoja.append([107, 2, "PIAZZA Mariano Sub. Of. Mayor.", None, 202 if repetido else 319, 1, "Córdoba, Nathan",
		             None, 608, 3, "AHUMADA Horacio, Of.Subinsp."])
		hoja.append(["BOMBEROS", None, None, None, "BRIGADA AUXILIAR"])
		hoja.append([201, 1, "Pautasso, Paulo", None, 401, 1, "LOPEZ, Flavia"])
		hoja.append(["=(A8+1)", 2, "Quintero Julian"])
		ruta = f"{carpeta}/listado.xlsx"
		libro.save(ruta)  # openpyxl no guarda el resultado de las fórmulas: el comando las resuelve
		return ruta

	def importar(self, ruta, *opciones):
		from io import StringIO
		from django.core.management import call_command

		call_command("importar_listado", ruta, "--anio", "2026", *opciones, stdout=StringIO(), stderr=StringIO())

	def test_lee_secciones_rangos_grupos_y_formulas(self):
		import tempfile
		from .models import Integrante, NumeroMerito

		with tempfile.TemporaryDirectory() as carpeta:
			ruta = self.planilla(carpeta)
			self.importar(ruta)
			self.importar(ruta)  # la segunda vez actualiza, no duplica
		self.assertEqual(Integrante.objects.count(), 8)
		jefe = Integrante.objects.get(numeros_merito__numero_merito=101)
		self.assertEqual((jefe.apellido, jefe.nombre, jefe.rango.nombre_rango, jefe.cargo),
		                 ("Nicola", "Gustavo", "Comisario General", "1° Jefe"))
		self.assertTrue(jefe.es_jefe_activo)
		self.assertIsNone(jefe.usuario)  # los usuarios los crea crear_usuarios_integrantes
		piazza = Integrante.objects.get(apellido="Piazza")
		self.assertEqual((piazza.rango.nombre_rango, piazza.grupo_guardia), ("Suboficial Mayor", 2))
		quintero = NumeroMerito.objects.get(anio=2026, numero_merito=202).integrante
		self.assertEqual((quintero.apellido, quintero.nombre), ("Quintero", "Julian"))
		self.assertEqual(Integrante.objects.get(apellido="Acuña").estado.nombre_estado, "Aspirante")
		self.assertEqual(Integrante.objects.get(apellido="Lopez").estado.nombre_estado, "Auxiliar")
		ahumada = Integrante.objects.get(apellido="Ahumada")
		self.assertEqual((ahumada.estado.nombre_estado, ahumada.rango.nombre_rango), ("Retirado", "Oficial Subinspector"))
		self.assertTrue(quintero.legajo.startswith("PROV-"))

	def test_no_crea_usuarios_ni_toca_admin(self):
		import tempfile
		from django.contrib.auth import get_user_model

		admin = get_user_model().objects.create_superuser("admin", "", "clave-admin-123")
		with tempfile.TemporaryDirectory() as carpeta:
			self.importar(self.planilla(carpeta))
		self.assertEqual(list(get_user_model().objects.values_list("username", flat=True)), ["admin"])
		self.assertFalse(hasattr(get_user_model().objects.get(pk=admin.pk), "integrante"))

	def test_numero_repetido_no_importa_nada(self):
		import tempfile
		from django.core.management.base import CommandError
		from .models import Integrante

		with tempfile.TemporaryDirectory() as carpeta:
			ruta = self.planilla(carpeta, repetido=True)
			with self.assertRaisesMessage(CommandError, "N° 202"):
				self.importar(ruta)
		self.assertFalse(Integrante.objects.exists())

	def test_rangos_viejos_se_renombran(self):
		from io import StringIO
		from django.core.management import call_command
		from .models import Rango

		Rango.objects.filter(nombre_rango="Comisario General").update(nombre_rango="Comandante General")
		call_command("cargar_catalogos", stdout=StringIO())
		self.assertFalse(Rango.objects.filter(nombre_rango="Comandante General").exists())
		self.assertTrue(Rango.objects.get(nombre_rango="Comisario General").es_cabeza)


class AltaMuestraGuardiaTests(TestCase):
	def test_subtitulo_con_numero_de_merito_y_opcion_vacia_en_castellano(self):
		from io import StringIO
		from django.core.management import call_command

		call_command("cargar_catalogos", "--demo", stdout=StringIO())
		self.client.login(username="guardia", password="bomberos2026")
		respuesta = self.client.get(reverse("crear_parte"))
		self.assertContains(respuesta, "Personal de guardia: <b>115 — Gómez, Lucía</b>")
		self.assertContains(respuesta, "Elegí una opción")
		self.assertNotContains(respuesta, ">—</option>")


class CierreSinMovilTests(TestCase):
	"""El móvil es opcional para cerrar; si se indicó, hace falta el km de regreso."""

	def setUp(self):
		from io import StringIO
		from django.core.management import call_command

		call_command("cargar_catalogos", "--demo", stdout=StringIO())

	def test_sin_movil_se_puede_cerrar_y_con_movil_pide_km(self):
		from .models import ParteSiniestro

		parte = ParteSiniestro.objects.filter(personal__isnull=False, unidad_salio=True).exclude(
			estado_parte__nombre_estado="Cerrado").distinct().first()
		movil = parte.vehiculo
		parte.vehiculo, parte.km_regreso = None, None
		parte.hora_regreso = None
		self.assertEqual(parte.problemas_para_cerrar(), ["Marcá el regreso de la unidad antes de cerrar el parte."])
		parte.hora_regreso = parte.hora_salida
		self.assertEqual(parte.problemas_para_cerrar(), [])
		parte.vehiculo = movil
		self.assertEqual(parte.problemas_para_cerrar(), ["Cargá el kilometraje de regreso."])

	def test_cerrar_sin_movil_desde_la_pantalla(self):
		from .models import ParteSiniestro

		parte = ParteSiniestro.objects.filter(personal__isnull=False, unidad_salio=True).exclude(
			estado_parte__nombre_estado="Cerrado").distinct().first()
		ParteSiniestro.objects.filter(pk=parte.pk).update(vehiculo=None, km_regreso=None, hora_regreso=parte.hora_salida)
		self.client.login(username="guardia", password="bomberos2026")
		respuesta = self.client.post(reverse("cerrar_parte", args=[parte.pk]), {"clave": "bomberos2026"})
		self.assertRedirects(respuesta, reverse("detalle_parte", args=[parte.pk]))
		parte.refresh_from_db()
		self.assertTrue(parte.esta_cerrado)
		# Cerrado ya no se corrige, ni siquiera el guardia: solo se ve.
		for vista in ("completar_parte", "corregir_parte"):
			self.assertRedirects(self.client.get(reverse(vista, args=[parte.pk])),
			                     reverse("detalle_parte", args=[parte.pk]), msg_prefix=vista)
		self.assertNotContains(self.client.get(reverse("detalle_parte", args=[parte.pk])),
		                       reverse("corregir_parte", args=[parte.pk]))

	def test_insignia_dice_solo_numero(self):
		self.client.login(username="guardia", password="bomberos2026")
		respuesta = self.client.get(reverse("mi_perfil"))
		self.assertContains(respuesta, "N° 115")
		self.assertNotContains(respuesta, "N° de mérito")


class ImprimirPartesTests(TestCase):
	"""Hoja para imprimir / guardar como PDF: un parte desde el detalle o todo el año desde el tomo."""

	def setUp(self):
		from io import StringIO
		from django.core.management import call_command

		call_command("cargar_catalogos", "--demo", stdout=StringIO())

	def test_hoja_de_un_parte(self):
		from .models import ParteSiniestro

		parte = ParteSiniestro.objects.first()
		self.client.login(username="guardia", password="bomberos2026")
		hoja = self.client.get(reverse("imprimir_parte", args=[parte.pk]))
		self.assertContains(hoja, f"Control de salida N° {parte.numero_correlativo}/{parte.anio}")
		self.assertContains(hoja, "Guardar como PDF")
		# El PDF está solo en el tomo, no en el detalle.
		self.assertNotContains(self.client.get(reverse("detalle_parte", args=[parte.pk])),
		                       reverse("imprimir_parte", args=[parte.pk]))
		self.assertContains(self.client.get(reverse("tomo", args=[parte.anio]) + "?ver=partes"),
		                    reverse("imprimir_parte", args=[parte.pk]))

	def test_todos_los_partes_del_anio_desde_el_tomo(self):
		from .models import ParteSiniestro

		anio = ParteSiniestro.objects.first().anio
		url = reverse("imprimir_partes_del_anio", args=[anio])
		self.client.force_login(get_user_model().objects.create_user("sinrol", password="x"))
		self.assertNotEqual(self.client.get(url).status_code, 200)  # sin rol no se ven los tomos
		self.assertNotEqual(self.client.get(reverse("imprimir_parte", args=[ParteSiniestro.objects.first().pk])).status_code, 200)
		self.client.login(username="guardia", password="bomberos2026")
		hojas = self.client.get(url)
		self.assertEqual(hojas.content.decode().count('class="card hoja"'),
		                 ParteSiniestro.objects.filter(anio=anio).count())
		self.assertContains(self.client.get(reverse("tomo", args=[anio]) + "?ver=partes"), url)


class ObservacionesTutorTests(TestCase):
	"""Observaciones del tutor del 28/10: menú del guardia, quién llama, códigos, esquinas, salida con
	unidad y autorización, personal activo con concurrencia y participación, triage, cobro, km y móviles."""

	def setUp(self):
		FlujoParteTests.setUp(self)
		from .models import Integrante
		self.jefe = Integrante.objects.get(legajo="1002")  # Comisario General
		self.guardia = Integrante.objects.get(legajo="1001")  # Sargento

	def marcar_salida(self, **extra):
		datos = {"salida-vehiculo": self.movil.pk, "salida-autorizo": self.jefe.pk,
		         "personal-integrantes": [self.bombero.pk]}
		datos.update(extra)
		return self.client.post(reverse("marcar_hora", args=[self.parte.pk, "salida"]), datos)

	def test_el_guardia_ve_y_carga_todos_los_partes(self):
		menu = self.client.get(reverse("menu"))
		for nombre in ("capacitaciones", "mantenimientos", "moviles"):
			self.assertContains(menu, f'href="{reverse(nombre)}"', msg_prefix=nombre)
		self.assertEqual(self.client.get(reverse("nueva_capacitacion")).status_code, 200)
		self.assertEqual(self.client.get(reverse("nuevo_mantenimiento")).status_code, 200)

	def test_codigos_de_tipo_y_motivo_y_motivos_de_accidente(self):
		from .models import Motivo, TipoSiniestro
		accidente = TipoSiniestro.objects.get(descripcion="Accidentes")
		self.assertEqual(str(accidente), "02 - Accidentes")
		self.assertEqual(str(Motivo.objects.get(codigo="114")), "114 - Colisión auto - moto / camión - moto / moto - moto")
		self.assertTrue(Motivo.objects.get(codigo="112").vale_para(accidente))
		self.assertContains(self.client.get(reverse("crear_parte")), "02 - Accidentes")

	def test_alta_guarda_quien_llama_y_una_esquina(self):
		from .models import Motivo, ParteSiniestro, TipoSiniestro
		datos = {"medio_llamado": "radio", "nombre_llamante": "Policía, móvil 12", "barrio": "Centro",
		         "tipo": TipoSiniestro.objects.get(descripcion="Accidentes").pk,
		         "motivo": Motivo.objects.get(codigo="114").pk}
		self.client.post(reverse("crear_parte"), {**datos, "direccion_input": "General Paz y Buenos Aires"})
		self.client.post(reverse("crear_parte"), {**datos, "direccion_input": "Mendoza", "es_esquina": "on",
		                                          "esquina_input": "Salta"})
		sin_otra = self.client.post(reverse("crear_parte"), {**datos, "direccion_input": "Mendoza", "es_esquina": "on"})

		junta, separada = ParteSiniestro.objects.filter(nombre_llamante="Policía, móvil 12").order_by("pk")
		self.assertEqual((junta.ubicacion_calle, junta.ubicacion_numero, junta.ubicacion_esquina),
		                 ("General Paz", "", "Buenos Aires"))
		self.assertEqual(junta.ubicacion, "General Paz y Buenos Aires")
		self.assertEqual(separada.ubicacion, "Mendoza y Salta")
		self.assertContains(sin_otra, "Escribí la otra calle de la esquina.")
		self.assertContains(self.client.get(reverse("detalle_parte", args=[junta.pk])), "Policía, móvil 12")

	def test_autorizo_solo_graduados_de_mayor_a_menor(self):
		form = self.client.get(reverse("detalle_parte", args=[self.parte.pk])).context["salida"]["form"]
		autorizan = list(form.fields["autorizo"].queryset)
		self.assertEqual(autorizan[0], self.jefe)
		self.assertIn(self.guardia, autorizan)
		self.assertNotIn(self.bombero, autorizan)  # Bombero: no es graduado

	def test_salida_pide_unidad_y_autorizacion_y_suma_personal(self):
		self.assertFalse(self.parte.unidad_salio)  # recién creado no figura como salido
		self.assertFalse(self.parte.linea_de_tiempo[1]["hecho"])
		self.client.post(reverse("marcar_hora", args=[self.parte.pk, "salida"]), {"salida-vehiculo": self.movil.pk})
		self.parte.refresh_from_db()
		self.assertIsNone(self.parte.hora_salida)  # sin autorización no sale

		self.marcar_salida()
		self.parte.refresh_from_db()
		self.assertTrue(self.parte.unidad_salio)
		self.assertIsNotNone(self.parte.hora_salida)
		self.assertEqual((self.parte.vehiculo, self.parte.autorizo), (self.movil, self.jefe))
		self.assertEqual(self.parte.personal.get().integrante, self.bombero)
		self.assertTrue(self.parte.linea_de_tiempo[1]["hecho"])

	def test_guardar_salida_sin_marcar_la_hora(self):
		self.marcar_salida(guardar="1")
		self.parte.refresh_from_db()
		self.assertIsNone(self.parte.hora_salida)
		self.assertEqual(self.parte.vehiculo, self.movil)
		pantalla = self.client.get(reverse("completar_parte", args=[self.parte.pk]))
		self.assertContains(pantalla, "Todavía no se marcó la salida")
		self.assertNotContains(pantalla, 'name="unidad_salio"')

	def test_no_se_cierra_sin_marcar_la_salida(self):
		self.client.post(reverse("completar_parte", args=[self.parte.pk]),
		                 FlujoParteTests.datos_completar(self, personal=[self.bombero], hora_salida=""))
		self.assertTrue(any("salida" in p for p in self.parte.problemas_para_cerrar()))
		self.client.post(reverse("corregir_parte", args=[self.parte.pk]), FlujoParteTests.datos_completar(
			self, personal=[self.bombero], salida_cancelada="on", hora_salida=""))
		self.parte.refresh_from_db()
		self.assertTrue(self.parte.salida_cancelada)
		self.assertEqual(self.parte.problemas_para_cerrar(), [])

	def test_personal_solo_activo_con_concurrencia_participacion_y_cobro(self):
		from .models import EstadoIntegrante, Integrante, TipoSiniestro
		retirado = Integrante.objects.get(legajo="1004")
		retirado.estado = EstadoIntegrante.objects.get(nombre_estado="Retirado")
		retirado.save()
		self.assertNotIn(retirado, self.client.get(reverse("completar_parte", args=[self.parte.pk]))
		                 .context["personal"].fields["integrantes"].queryset)

		TipoSiniestro.objects.filter(pk=self.parte.tipo_id).update(se_cobra=True)
		b = self.bombero.pk
		self.client.post(reverse("completar_parte", args=[self.parte.pk]), FlujoParteTests.datos_completar(
			self, personal=[self.bombero], **{f"personal-conc_{b}": "en_cuartel", f"personal-part_{b}": "apresto",
			                                  f"personal-cobro_{b}": "on"}))
		fila = self.parte.personal.get()
		self.assertEqual((fila.tipo_concurrencia, fila.tipo_participacion, fila.cobro), ("en_cuartel", "apresto", True))
		self.assertContains(self.client.get(reverse("detalle_parte", args=[self.parte.pk])), "<th>Cobró</th>")

	def test_triage_marca_herido_y_sin_lesiones_no(self):
		from .models import CentroSalud, Damnificado
		CentroSalud.objects.get_or_create(nombre="Hospital Regional Louis Pasteur")
		datos = FlujoParteTests.datos_completar(self, **{
			"damnificados-TOTAL_FORMS": 3, "damnificados-INITIAL_FORMS": 0,
			"damnificados-0-apellido_nombre": "Paz, Juan", "damnificados-0-triage": "verde",
			"damnificados-0-trasladado": "on", "damnificados-0-hacia": "otro",
			"damnificados-0-hacia_otro": "Clínica de Villa Nueva", "damnificados-0-recibe_matricula": "MP 1234",
			"damnificados-0-recibe_firma": "data:image/png;base64,iVBORw0KGgo=",
			"damnificados-1-apellido_nombre": "Luna, Ana", "damnificados-1-triage": "sin_lesiones",
			"damnificados-2-apellido_nombre": "Sosa, Eva"})
		self.client.post(reverse("completar_parte", args=[self.parte.pk]), datos)

		juan, ana, eva = (Damnificado.objects.get(apellido_nombre=n) for n in ("Paz, Juan", "Luna, Ana", "Sosa, Eva"))
		self.assertTrue(juan.herido)
		self.assertFalse(ana.herido)
		self.assertEqual((eva.triage, eva.herido), ("", False))
		self.assertEqual(juan.destino, "Clínica de Villa Nueva")
		self.assertEqual(juan.recibe_matricula, "MP 1234")
		pantalla = self.client.get(reverse("completar_parte", args=[self.parte.pk]))
		self.assertNotContains(pantalla, "Contacto y domicilio (opcional)")  # el contacto siempre a la vista
		self.assertContains(pantalla, '<datalist id="provincias">')

	def test_km_mayor_al_de_otros_registros_del_movil(self):
		from datetime import date
		from .models import Capacitacion
		Capacitacion.objects.create(tema="Manejo", fecha=date(self.parte.anio, 1, 1), vehiculo=self.movil,
		                            km_regreso=self.movil.kilometraje_actual + 500)
		respuesta = self.client.post(reverse("completar_parte", args=[self.parte.pk]), FlujoParteTests.datos_completar(
			self, km_regreso=self.movil.kilometraje_actual + 100))
		self.assertContains(respuesta, f"({self.movil.kilometraje_actual + 500} km)")

	def test_moviles_los_carga_estadistica(self):
		from .models import Combustible, TipoVehiculo, Vehiculo
		self.assertEqual(self.client.get(reverse("moviles")).status_code, 200)  # el guardia los ve
		self.assertRedirects(self.client.get(reverse("nuevo_movil")), reverse("menu"))
		self.client.login(username="estadistica", password="bomberos2026")
		self.client.post(reverse("nuevo_movil"), {
			"legajo": "9", "tipo_vehiculo": TipoVehiculo.objects.first().pk, "marca": "Iveco",
			"combustible": Combustible.objects.first().pk, "kilometraje_actual": 1000, "en_servicio": "on"})
		self.assertTrue(Vehiculo.objects.filter(legajo="9", marca="Iveco").exists())

	def test_capacitacion_a_cargo_ayudantes_en_grilla_y_cobro(self):
		from .models import Capacitacion
		pantalla = self.client.get(reverse("nueva_capacitacion"))
		self.assertContains(pantalla, 'id="buscar-ayudantes"')
		self.client.post(reverse("nueva_capacitacion"), {
			"datos": "Rescate vehicular", "fecha": f"{self.parte.anio}-05-10", "modalidad": "interna",
			"sentido": "dictada", "nivel": "cuartel", "a_cargo_integrante": self.jefe.pk,
			"ayudantes": [self.guardia.pk], "asistentes": [self.bombero.pk], f"cobro_{self.bombero.pk}": "on"})
		capacitacion = Capacitacion.objects.get(tema="Rescate vehicular")
		self.assertEqual(capacitacion.instructor, str(self.jefe))
		self.assertEqual(list(capacitacion.ayudantes.all()), [self.guardia])
		self.assertTrue(capacitacion.asistencias.get().cobro)

	def test_e100_se_elige_de_su_tabla(self):
		from .models import PersonalE100
		activo = PersonalE100.objects.create(apellido_nombre="Ruiz, Marta")
		PersonalE100.objects.create(apellido_nombre="Viejo, Juan", activo=False)
		pantalla = self.client.get(reverse("completar_parte", args=[self.parte.pk]))
		self.assertContains(pantalla, "Ruiz, Marta")
		self.assertNotContains(pantalla, "Viejo, Juan")
		self.client.post(reverse("completar_parte", args=[self.parte.pk]), FlujoParteTests.datos_completar(
			self, personal_e100=activo.pk))
		self.parte.refresh_from_db()
		self.assertEqual(self.parte.personal_e100, activo)


class PedidosDelUsuarioTests(TestCase):
	"""Pedidos del 01/11: mapa, calles, médico con firma del día, quitar rodados, planillas y móviles."""

	FIRMA = "data:image/png;base64,iVBORw0KGgo="

	def setUp(self):
		FlujoParteTests.setUp(self)

	def test_mapa_con_punto_decimal(self):
		from .models import ParteSiniestro
		ParteSiniestro.objects.filter(pk=self.parte.pk).update(latitud="-32.410000", longitud="-63.240000")
		pantalla = self.client.get(reverse("detalle_parte", args=[self.parte.pk]))
		self.assertContains(pantalla, "maps?q=-32.410000,-63.240000")

	def test_calle_agregada_por_estadistica_se_ofrece_y_normaliza(self):
		from .geocodificacion import calles, nombre_oficial
		from .models import Calle
		self.assertRedirects(self.client.get(reverse("calles")), reverse("menu"))  # el guardia no
		self.client.login(username="estadistica", password="bomberos2026")
		self.client.post(reverse("calles"), {"nombre": "Pasaje de Prueba Inventado"})
		self.assertIn("Pasaje de Prueba Inventado", calles())
		self.assertEqual(nombre_oficial("pasaje de prueba inventado"), "Pasaje de Prueba Inventado")
		repetida = self.client.post(reverse("calles"), {"nombre": "manuel belgrano"})
		self.assertContains(repetida, "Ya está en el listado")
		self.client.post(reverse("quitar_calle", args=[Calle.objects.get().pk]))
		self.assertFalse(Calle.objects.exists())

	def test_georef_suma_solo_calles_nuevas_y_prolijas(self):
		from .management.commands import actualizar_calles as comando
		from unittest import mock
		calles = {"Aldo Lisignoli", "General Paz", "27 de Setiembre"}
		respuesta = {"calles": [{"nombre": n} for n in (
			"A LISIGNOLI", "GRAL PAZ", "27 DE SEPTIEMBRE", "CALLE S N", "INT ARINES", "RCA DE ISRAEL")]}
		with mock.patch.object(comando, "_pedir", return_value=respuesta):
			agregadas = comando.sumar_georef(calles, "14042170")
		self.assertEqual(agregadas, 2)
		self.assertIn("Intendente Arines", calles)
		self.assertIn("República de Israel", calles)

	def damnificado(self, indice, nombre, **extra):
		datos = {f"damnificados-{indice}-apellido_nombre": nombre, f"damnificados-{indice}-trasladado": "on",
		         f"damnificados-{indice}-recibe_dr": "Dr. House"}
		datos.update({f"damnificados-{indice}-{k}": v for k, v in extra.items()})
		return datos

	def test_medico_se_recuerda_pero_firma_en_cada_damnificado(self):
		from .models import Damnificado, Medico
		datos = FlujoParteTests.datos_completar(self, **{
			"damnificados-TOTAL_FORMS": 2, "damnificados-INITIAL_FORMS": 0,
			**self.damnificado(0, "Paz, Juan"),
			**self.damnificado(1, "Luna, Ana", recibe_matricula="MP 99", recibe_firma=self.FIRMA)})
		self.client.post(reverse("completar_parte", args=[self.parte.pk]), datos)
		self.assertEqual(Medico.objects.get(nombre="Dr. House").matricula, "MP 99")
		self.assertEqual(Damnificado.objects.get(apellido_nombre="Luna, Ana").recibe_firma, self.FIRMA)
		self.assertEqual(Damnificado.objects.get(apellido_nombre="Paz, Juan").recibe_firma, "")
		pantalla = self.client.get(reverse("corregir_parte", args=[self.parte.pk]))
		self.assertNotContains(pantalla, "data-aviso-firma")
		otro = Damnificado.objects.create(parte=self.parte, apellido_nombre="Sosa, Eva", recibe_dr="dr. house")
		self.assertEqual((otro.recibe_firma, otro.recibe_matricula), ("", "MP 99"))

	def test_quitar_rodado_guardado_no_vuelve(self):
		from .models import Damnificado, VehiculoSiniestrado
		rodado = VehiculoSiniestrado.objects.create(parte=self.parte, rodado_numero=1, marca="Ford")
		Damnificado.objects.create(parte=self.parte, vehiculo_siniestrado=rodado, apellido_nombre="Paz, Juan")
		pantalla = self.client.get(reverse("corregir_parte", args=[self.parte.pk]))
		self.assertContains(pantalla, "data-quitar-fila")
		fila = pantalla.context["damnificados"].forms[0]
		datos = FlujoParteTests.datos_completar(self, **{
			"rodados-TOTAL_FORMS": 1, "rodados-INITIAL_FORMS": 1, "rodados-0-id": rodado.pk, "rodados-0-rodado_numero": 1,
			"rodados-0-parte": self.parte.pk, "rodados-0-DELETE": "on",
			"damnificados-TOTAL_FORMS": 1, "damnificados-INITIAL_FORMS": 1, "damnificados-0-id": fila.instance.pk,
			"damnificados-0-apellido_nombre": "Paz, Juan", "damnificados-0-rodado_numero": 1})
		self.client.post(reverse("corregir_parte", args=[self.parte.pk]), datos)
		self.assertFalse(VehiculoSiniestrado.objects.filter(parte=self.parte).exists())

	def test_incendio_igual_permite_cargar_rodados_desde_el_detalle(self):
		detalle = self.client.get(reverse("detalle_parte", args=[self.parte.pk]))
		self.assertContains(detalle, "#seccion-rodados")
		self.assertContains(self.client.get(reverse("corregir_parte", args=[self.parte.pk])), 'id="seccion-rodados"')

	def test_parte_cerrado_por(self):
		self.client.post(reverse("corregir_parte", args=[self.parte.pk]), FlujoParteTests.datos_completar(
			self, personal=[self.bombero], km_regreso=self.movil.kilometraje_actual + 5))
		FlujoParteTests.cerrar(self)
		pantalla = self.client.get(reverse("detalle_parte", args=[self.parte.pk]))
		self.assertContains(pantalla, "Parte cerrado por")
		self.assertNotContains(pantalla, "Firmado por")

	def test_capacitacion_como_la_planilla(self):
		from .models import Capacitacion
		pantalla = self.client.get(reverse("nueva_capacitacion"))
		for texto in ("Datos de la capacitación", "Pasantías de Guardia", "Aspirantes 2do año", "Aspirantes 3er año",
		              "Capacitación de departamento", "Dto. ERA", "Km de regreso"):
			self.assertContains(pantalla, texto)
		self.assertNotContains(pantalla, "Observaciones")
		base = {"fecha": f"{self.parte.anio}-05-10", "modalidad": "interna", "sentido": "dictada", "nivel": "cuartel"}
		sin_datos = self.client.post(reverse("nueva_capacitacion"), base)
		self.assertContains(sin_datos, "Anotá los datos de la capacitación.")
		self.client.post(reverse("nueva_capacitacion"), {
			**base, "datos": "Primer renglon\nSegundo", "guardia_desde": "08:00", "guardia_hasta": "12:00",
			"vehiculo": self.movil.pk, "km_regreso": self.movil.kilometraje_actual + 9})  # Unidad: No
		capacitacion = Capacitacion.objects.get(tema="Primer renglon")
		self.assertIsNone(capacitacion.vehiculo)
		self.assertIsNone(capacitacion.km_regreso)
		self.assertIsNotNone(capacitacion.guardia_desde)

	def test_mantenimiento_en_el_orden_de_la_planilla(self):
		pantalla = self.client.get(reverse("nuevo_mantenimiento")).content.decode()
		for antes, despues in (("Rep. autorizada por", "Vehículo en reparación"), ("Vehículo en reparación", "Datos mant. efectuado"),
		                       ("Datos mant. efectuado", "Motivo del mismo"), ("Observaciones", "Controló")):
			self.assertLess(pantalla.index(antes), pantalla.index(despues), antes)
		self.assertNotIn('name="kilometraje"', pantalla)

	def test_movil_ficha_baja_y_quitar(self):
		from .models import Combustible, TipoVehiculo, Vehiculo
		self.client.login(username="estadistica", password="bomberos2026")
		self.assertContains(self.client.get(reverse("detalle_movil", args=[self.movil.pk])), "Dar de baja")
		nuevo = Vehiculo.objects.create(legajo="77", tipo_vehiculo=TipoVehiculo.objects.first(), marca="Iveco",
		                                combustible=Combustible.objects.first())
		self.client.post(reverse("baja_movil", args=[nuevo.pk]))
		nuevo.refresh_from_db()
		self.assertTrue(nuevo.dado_de_baja)
		self.client.post(reverse("quitar_movil", args=[nuevo.pk]))
		self.assertFalse(Vehiculo.objects.filter(pk=nuevo.pk).exists())
		con_historial = Vehiculo.objects.get(legajo="1")  # el del parte de la demo
		self.client.post(reverse("quitar_movil", args=[con_historial.pk]))
		self.assertTrue(Vehiculo.objects.filter(pk=con_historial.pk).exists())

	def test_centros_de_salud_y_otro(self):
		from .models import CentroSalud
		self.assertTrue(CentroSalud.objects.filter(nombre="Sanatorio Cruz Azul").exists())
		self.assertContains(self.client.get(reverse("corregir_parte", args=[self.parte.pk])), '<option value="otro"')


class CodigosYVisibilidadTests(TestCase):
	"""Códigos oficiales de intervención y partes cerrados solo para guardia, Estadística y jefe."""

	@classmethod
	def setUpTestData(cls):
		from io import StringIO
		from django.contrib.auth.models import Group
		from django.core.management import call_command
		from .models import EstadoParte, ParteSiniestro

		call_command("cargar_catalogos", "--demo", stdout=StringIO())
		cls.bombero = get_user_model().objects.create_user("b1", password="clave-segura-123")
		cls.bombero.groups.add(Group.objects.get(name="Bombero"))
		cls.abierto = ParteSiniestro.objects.exclude(estado_parte__nombre_estado="Cerrado").first()
		cls.cerrado = ParteSiniestro.objects.filter(estado_parte=EstadoParte.objects.get(nombre_estado="Cerrado")).first()

	def test_catalogo_oficial(self):
		from .models import Motivo, TipoSiniestro

		self.assertEqual(list(TipoSiniestro.objects.values_list("codigo", "descripcion", "parte")), [
			("01", "Incendio", "siniestro"), ("02", "Accidentes", "siniestro"),
			("03", "Salvataje", "siniestro"), ("04", "Rescate", "siniestro"),
			("05", "Tareas generales", "general"), ("06", "Organización funcional", "general"),
			("07", "Mantenimientos", "mantenimiento"), ("08", "Capacitaciones", "capacitacion"),
			("09", "Actividades comerciales", "actividad_comercial")])
		# 89 códigos de la planilla: 56 en uso y 33 "Reserva", que quedan apagados.
		self.assertEqual(Motivo.objects.count(), 89)
		self.assertEqual(Motivo.objects.filter(activo=True).count(), 56)
		self.assertEqual(Motivo.objects.get(codigo="121").tipos.get().codigo, "03")
		self.assertEqual(TipoSiniestro.objects.get(codigo="03").lista_secciones(), ["damnificados"])
		# Columna Detalle de la planilla: el texto de ayuda del motivo.
		self.assertEqual(Motivo.objects.get(codigo="122").detalle, "Herida de arma, robo, riña")

	def test_el_parte_de_siniestro_ofrece_solo_sus_tipos_y_motivos_activos(self):
		from .forms import ParteSiniestroForm
		from .models import Motivo, TipoSiniestro

		form = ParteSiniestroForm()
		tipos = form.fields["tipo"].queryset
		self.assertEqual([t.codigo for t in tipos], ["01", "02", "03", "04"])
		motivos = form.fields["motivo"].queryset
		# Los de siniestro que estén activos: ni los "Reserva" ni los de las otras planillas.
		self.assertIn(Motivo.objects.get(codigo="121"), motivos)
		self.assertNotIn(Motivo.objects.get(codigo="130"), motivos)   # Reserva 130
		self.assertNotIn(Motivo.objects.get(codigo="171"), motivos)   # Capacitación general

	def test_un_motivo_apagado_que_el_parte_ya_tenia_se_sigue_ofreciendo(self):
		from .forms import CompletarParteForm
		from .models import Motivo, ParteSiniestro

		reserva = Motivo.objects.get(codigo="130")
		# Se relee el parte para no dejar tocada la instancia que comparten los demás tests.
		parte = ParteSiniestro.objects.get(pk=self.abierto.pk)
		parte.motivo = reserva
		parte.save(update_fields=["motivo"])
		form = CompletarParteForm(instance=parte)
		self.assertIn(reserva, form.fields["motivo"].queryset)

	def test_lo_editado_en_el_admin_no_se_pisa(self):
		from io import StringIO
		from django.core.management import call_command
		from .models import Motivo

		Motivo.objects.filter(codigo="101").update(descripcion="Vivienda familiar")
		call_command("cargar_catalogos", stdout=StringIO())
		self.assertEqual(Motivo.objects.get(codigo="101").descripcion, "Vivienda familiar")
		self.assertFalse(Motivo.objects.filter(descripcion="Fuego en vivienda").exists())

	def test_bombero_solo_ve_los_partes_en_curso(self):
		self.client.force_login(self.bombero)
		listado = self.client.get(reverse("listado_partes"))
		self.assertIn(self.abierto, listado.context["partes"])
		self.assertNotIn(self.cerrado, listado.context["partes"])
		self.assertNotContains(listado, "estado=cerrados")
		self.assertNotIn(self.cerrado, self.client.get(reverse("listado_partes"), {"estado": "cerrados"}).context["partes"])
		self.assertEqual(self.client.get(reverse("detalle_parte", args=[self.abierto.pk])).status_code, 200)
		self.assertRedirects(self.client.get(reverse("detalle_parte", args=[self.cerrado.pk])), reverse("listado_partes"))

	def test_guardia_estadistica_y_jefe_ven_los_cerrados(self):
		for usuario in ("guardia", "estadistica", "jefe"):
			self.client.login(username=usuario, password="bomberos2026")
			self.assertEqual(self.client.get(reverse("detalle_parte", args=[self.cerrado.pk])).status_code, 200, usuario)
			self.assertIn(self.cerrado, self.client.get(reverse("listado_partes")).context["partes"], usuario)

	def test_el_jefe_puede_lo_del_guardia_y_lo_de_estadistica(self):
		self.client.login(username="jefe", password="bomberos2026")
		for vista in ("crear_parte", "tomos", "nueva_capacitacion", "nuevo_mantenimiento", "nuevo_bombero"):
			self.assertEqual(self.client.get(reverse(vista)).status_code, 200, vista)


class RetoquesDelDocTests(TestCase):
	"""Observaciones del doc del tutor (05/11) sobre el parte."""

	def setUp(self):
		from io import StringIO
		from django.core.management import call_command
		from .models import EstadoParte, Motivo, ParteSiniestro, TipoSiniestro

		call_command("cargar_catalogos", "--demo", stdout=StringIO())
		self.client.login(username="guardia", password="bomberos2026")
		self.parte = ParteSiniestro.objects.exclude(estado_parte__nombre_estado="Cerrado").first()

	def test_linea_de_tiempo_con_fecha_y_hora(self):
		from datetime import time, timedelta
		from .models import ParteSiniestro

		ParteSiniestro.objects.filter(pk=self.parte.pk).update(
			hora_llamada=time(23, 50), hora_salida=time(23, 55), hora_llegada=time(0, 5), hora_regreso=None)
		self.parte.refresh_from_db()
		pasos = {p["nombre"]: p for p in self.parte.linea_de_tiempo}
		self.assertEqual(list(pasos), ["Llamada", "Salida", "Llegada", "Retorno", "Cerrado"])
		self.assertEqual(pasos["Salida"]["fecha"], self.parte.fecha)
		self.assertEqual(pasos["Llegada"]["fecha"], self.parte.fecha + timedelta(days=1))  # cruzó la medianoche
		self.assertFalse(pasos["Retorno"]["hecho"])
		detalle = self.client.get(reverse("detalle_parte", args=[self.parte.pk]))
		self.assertContains(detalle, self.parte.fecha.strftime("%d/%m/%Y") + " 23:55")
		self.assertContains(detalle, "Registro de movimiento de la unidad")

	def test_lesion_obligatoria_para_cerrar_y_textos(self):
		from .models import Damnificado

		sano = Damnificado.objects.create(parte=self.parte, apellido_nombre="Sin Triage, Ana")
		herido = Damnificado.objects.create(parte=self.parte, apellido_nombre="Paz, Juan", triage="rojo", sexo="M")
		self.assertIn("Falta la descripción de la lesión de Paz, Juan.", self.parte.problemas_para_cerrar())
		self.assertFalse(any("Sin Triage" in p for p in self.parte.problemas_para_cerrar()))
		herido.lesion = "Trauma de cráneo"
		herido.save()
		self.assertFalse(any("lesión" in p for p in self.parte.problemas_para_cerrar()))
		detalle = self.client.get(reverse("detalle_parte", args=[self.parte.pk]))
		self.assertContains(detalle, "Sin criterio de atención médica")
		self.assertContains(detalle, "Trauma de cráneo")
		self.assertNotContains(detalle, "Sin triage")
		self.assertTrue(sano.pk)

	def test_incendio_con_otros_pide_descripcion(self):
		from .forms import IncendioDatoForm
		from .models import IncendioDato

		self.assertIn(("Otros", "Otros"), IncendioDatoForm().fields["lo_danado"].choices)
		incendio = IncendioDato.objects.create(parte=self.parte, tipo_bien_danado="Casa, Otros")
		self.parte.refresh_from_db()
		self.assertTrue(any("Otros" in p for p in self.parte.problemas_para_cerrar()))
		incendio.descripcion = "Un cartel"
		incendio.save()
		self.parte.refresh_from_db()
		self.assertFalse(any("Otros" in p for p in self.parte.problemas_para_cerrar()))

	def test_dni_en_mayusculas_y_chofer(self):
		from .forms import NegativaAtencionForm
		from .models import PartePersonal

		form = NegativaAtencionForm(parte=self.parte)
		self.assertEqual(form.fields["dni_firmante"].label, "DNI firmante")
		self.assertEqual(form.fields["testigo_dni"].label, "Testigo DNI")
		self.assertIn(("chofer", "Chofer"), PartePersonal.Participacion.choices)

	def test_rango_al_momento_del_parte(self):
		from .models import Integrante, PartePersonal, Rango

		fila = PartePersonal.objects.filter(parte=self.parte).first()
		anterior = fila.rango
		self.assertEqual(anterior, fila.integrante.rango)
		Integrante.objects.filter(pk=fila.integrante_id).update(rango=Rango.objects.exclude(pk=anterior.pk).first())
		fila.refresh_from_db()
		self.assertEqual(fila.rango, anterior)
		self.assertContains(self.client.get(reverse("detalle_parte", args=[self.parte.pk])), anterior.nombre_rango)

	def test_alerta_de_partes_sin_cerrar_y_lista_por_guardia(self):
		from datetime import timedelta
		from django.utils import timezone
		from .models import ParteSiniestro

		self.assertNotContains(self.client.get(reverse("menu")), "sin cerrar hace más de")
		ParteSiniestro.objects.filter(pk=self.parte.pk).update(fecha=timezone.localdate() - timedelta(days=2))
		self.assertContains(self.client.get(reverse("menu")), "sin cerrar hace más de 24 horas")
		listado = self.client.get(reverse("listado_partes"))
		self.assertContains(listado, "sin cerrar hace más de 24 horas")
		self.assertEqual(list(self.client.get(reverse("listado_partes"), {"estado": "demorados"}).context["partes"]), [self.parte])
		por_guardia = self.client.get(reverse("listado_partes"), {"estado": "por_guardia"})
		self.assertContains(por_guardia, f"{self.parte.guardia.apellido}, {self.parte.guardia.nombre} (")
		self.assertNotIn("Cerrado", [p.estado_parte.nombre_estado for p in por_guardia.context["partes"]])

	def test_autorefresco_cambia_la_version_al_marcar_una_hora(self):
		url = reverse("version_partes") + f"?parte={self.parte.pk}"
		antes = self.client.get(url).json()["version"]
		detalle = self.client.get(reverse("detalle_parte", args=[self.parte.pk]))
		self.assertContains(detalle, antes)
		self.assertEqual(self.client.get(url).json()["version"], antes)
		self.parte.hora_regreso = "12:30"
		self.parte.save()
		self.assertNotEqual(self.client.get(url).json()["version"], antes)
		self.assertContains(self.client.get(reverse("listado_partes")), reverse("version_partes"))


class ListadoPorFechaTests(TestCase):
	"""Partes del día de entrada y árbol año > mes > día (pedido del tutor)."""

	def setUp(self):
		from io import StringIO
		from datetime import date
		from django.core.management import call_command
		from .models import ParteSiniestro

		call_command("cargar_catalogos", "--demo", stdout=StringIO())
		self.client.login(username="guardia", password="bomberos2026")
		self.viejo = ParteSiniestro.objects.order_by("pk").first()
		ParteSiniestro.objects.filter(pk=self.viejo.pk).update(fecha=date(2025, 3, 9))
		self.viejo.refresh_from_db()

	def test_de_entrada_los_partes_de_hoy(self):
		from django.utils import timezone
		from .models import ParteSiniestro

		listado = self.client.get(reverse("listado_partes"))
		self.assertEqual(set(listado.context["partes"]), set(ParteSiniestro.objects.filter(fecha=timezone.localdate())))
		self.assertNotIn(self.viejo, listado.context["partes"])
		self.assertContains(listado, "Hoy, " + timezone.localdate().strftime("%d/%m/%Y"))

	def test_arbol_y_filtros_por_anio_mes_y_dia(self):
		listado = self.client.get(reverse("listado_partes"))
		anios = [a["anio"] for a in listado.context["arbol"]]
		self.assertIn(2025, anios)
		self.assertContains(self.client.get(reverse("listado_partes"), {"fecha": "todas"}), "fecha=2025")
		self.assertContains(self.client.get(reverse("listado_partes"), {"fecha": "2025-03"}), "fecha=2025-03-09")
		for fecha in ("2025", "2025-03", "2025-03-09"):
			self.assertEqual(list(self.client.get(reverse("listado_partes"), {"fecha": fecha}).context["partes"]), [self.viejo], fecha)
		self.assertContains(self.client.get(reverse("listado_partes"), {"fecha": "2025-03"}), "Marzo 2025")
		self.assertIn(self.viejo, self.client.get(reverse("listado_partes"), {"fecha": "todas"}).context["partes"])
		self.assertIn(self.viejo, self.client.get(reverse("listado_partes"), {"anio": "2025"}).context["partes"])  # enlace del tomo

	def test_buscar_y_sin_cerrar_miran_todas_las_fechas(self):
		self.assertIn(self.viejo, self.client.get(reverse("listado_partes"), {"q": str(self.viejo.numero_correlativo)}).context["partes"])
		if not self.viejo.esta_cerrado:
			self.assertIn(self.viejo, self.client.get(reverse("listado_partes"), {"estado": "abiertos"}).context["partes"])
		self.assertEqual(self.client.get(reverse("listado_partes"), {"fecha": "2025-02-30"}).context["titulo_fecha"], "Todas las fechas")


class CorreccionDeCerradosTests(TestCase):
	"""El jefe (admin) corrige un parte cerrado: motivo obligatorio, contraseña y registro de qué cambió."""

	def setUp(self):
		from io import StringIO
		from django.core.management import call_command
		from .models import ParteSiniestro, Vehiculo

		call_command("cargar_catalogos", "--demo", stdout=StringIO())
		self.parte = ParteSiniestro.objects.filter(estado_parte__nombre_estado="Cerrado").first()
		self.movil = self.parte.vehiculo or Vehiculo.objects.first()
		self.url = reverse("corregir_parte", args=[self.parte.pk])

	def datos(self, **extra):
		return FlujoParteTests.datos_completar(
			self, personal=[pp.integrante for pp in self.parte.personal.all()],
			km_regreso=self.parte.km_regreso or "", **extra)

	def test_guardia_y_bombero_no_corrigen_un_cerrado(self):
		self.client.login(username="guardia", password="bomberos2026")
		self.assertRedirects(self.client.get(self.url), reverse("detalle_parte", args=[self.parte.pk]))
		self.assertNotContains(self.client.get(reverse("detalle_parte", args=[self.parte.pk])), self.url)

	def test_jefe_corrige_con_motivo_y_queda_registrado(self):
		from .models import Firma

		cierres = Firma.objects.count()
		self.client.login(username="jefe", password="bomberos2026")
		self.assertContains(self.client.get(reverse("detalle_parte", args=[self.parte.pk])), self.url)
		pantalla = self.client.get(self.url)
		self.assertContains(pantalla, "Motivo de la corrección")
		self.assertContains(pantalla, 'data-solo-con-senal="1"')

		respuesta = self.client.post(self.url, self.datos(observaciones="Se agrega lo que informó la policía",
		                                                  motivo_correccion="Faltaba el dato que pasó la policía",
		                                                  clave="bomberos2026"))
		self.assertRedirects(respuesta, reverse("detalle_parte", args=[self.parte.pk]))
		self.parte.refresh_from_db()
		self.assertTrue(self.parte.esta_cerrado)
		self.assertEqual(self.parte.observaciones, "Se agrega lo que informó la policía")
		correccion = Firma.de(self.parte, Firma.Accion.CORRECCION_PARTE).get()
		self.assertEqual(correccion.datos["motivo"], "Faltaba el dato que pasó la policía")
		self.assertEqual(correccion.datos["cambios"]["observaciones"][1], "Se agrega lo que informó la policía")
		self.assertEqual(Firma.objects.exclude(accion=Firma.Accion.CORRECCION_PARTE).count(), cierres)  # el sello del cierre no se toca
		self.assertEqual(Firma.verificar_cadena(), [])
		detalle = self.client.get(reverse("detalle_parte", args=[self.parte.pk]))
		self.assertContains(detalle, "Corregido después del cierre")
		self.assertContains(detalle, correccion.codigo)

	def test_sin_motivo_o_con_clave_mala_no_se_guarda(self):
		from .models import Firma

		self.client.login(username="jefe", password="bomberos2026")
		for extra in ({"clave": "bomberos2026"}, {"motivo_correccion": "Un motivo bien largo", "clave": "otra"}):
			respuesta = self.client.post(self.url, self.datos(observaciones="No tendría que quedar", **extra))
			self.assertEqual(respuesta.status_code, 200)
		self.parte.refresh_from_db()
		self.assertNotEqual(self.parte.observaciones, "No tendría que quedar")
		self.assertFalse(Firma.de(self.parte, Firma.Accion.CORRECCION_PARTE).exists())


class MantenimientoGeneralYE100Tests(TestCase):
	"""Mantenimiento de cualquier cosa del cuartel y tabla propia del personal E-100 (doc del tutor)."""

	def setUp(self):
		from io import StringIO
		from django.core.management import call_command

		call_command("cargar_catalogos", "--demo", stdout=StringIO())

	def test_mantenimiento_sin_movil_pide_que_se_mantuvo(self):
		from django.core.exceptions import ValidationError
		from .models import Mantenimiento

		sin_nada = Mantenimiento(tipo=Mantenimiento.Tipo.CORRECTIVO)
		with self.assertRaises(ValidationError):
			sin_nada.full_clean()
		bomba = Mantenimiento.objects.create(tipo=Mantenimiento.Tipo.CORRECTIVO, elemento="Bomba de agua del cuartel")
		self.assertEqual(bomba.que, "Bomba de agua del cuartel")
		self.client.login(username="guardia", password="bomberos2026")
		self.assertContains(self.client.get(reverse("detalle_mantenimiento", args=[bomba.pk])), "Bomba de agua del cuartel")
		otros = self.client.get(reverse("mantenimientos"), {"movil": "otros"})
		self.assertEqual(list(otros.context["mantenimientos"]), [bomba])
		self.assertContains(otros, "<h1 style=\"margin:0;\">Mantenimiento</h1>", html=False)

	def test_e100_lo_carga_estadistica(self):
		from .models import PersonalE100

		self.client.login(username="guardia", password="bomberos2026")
		self.assertNotEqual(self.client.get(reverse("personal_e100")).status_code, 200)
		self.client.login(username="estadistica", password="bomberos2026")
		self.assertContains(self.client.get(reverse("menu")), reverse("personal_e100"))
		self.client.post(reverse("personal_e100"), {"apellido_nombre": "Ruiz, Marta", "dni": "30.111.222",
		                                           "telefono": "", "fecha_asignacion": "2026-11-01", "activo": "on"})
		marta = PersonalE100.objects.get(apellido_nombre="Ruiz, Marta")
		self.assertEqual(marta.dni, "30111222")
		self.client.post(reverse("personal_e100") + f"?editar={marta.pk}", {
			"apellido_nombre": "Ruiz, Marta", "dni": "30111222", "telefono": "", "fecha_asignacion": "2026-11-01",
			"hasta": "2026-11-30"})
		marta.refresh_from_db()
		self.assertFalse(marta.activo)
		self.assertContains(self.client.get(reverse("personal_e100")), "30/11/2026")





class LibroDeGuardiaTests(TestCase):

	"""Libro de guardia: solo se agrega, queda encadenado y solo el jefe corrige (con una anotación nueva)."""



	def setUp(self):

		from io import StringIO

		from django.core.management import call_command



		call_command("cargar_catalogos", "--demo", stdout=StringIO())



	def test_el_guardia_anota_y_no_se_modifica_ni_se_borra(self):

		from .models import AnotacionLibroGuardia, FirmaInmutable



		self.client.login(username="guardia", password="bomberos2026")

		self.client.post(reverse("libro_guardia"), {"hora": "06:00", "texto": "Ingresa a la guardia el Bv. Turaglio F."})

		self.client.post(reverse("libro_guardia"), {"hora": "07:10", "texto": "Sale U°6 por un 02-111."})

		primera, segunda = AnotacionLibroGuardia.objects.order_by("pk")

		self.assertEqual((primera.numero, segunda.numero), (1, 2))

		self.assertEqual(segunda.hash_anterior, primera.hash)

		self.assertContains(self.client.get(reverse("libro_guardia")), "Sale U°6 por un 02-111.")

		primera.texto = "Otra cosa"

		with self.assertRaises(FirmaInmutable):

			primera.save()

		with self.assertRaises(FirmaInmutable):

			segunda.delete()

		AnotacionLibroGuardia.objects.filter(pk=primera.pk).update(texto="Cambiado desde la base")

		self.assertEqual(AnotacionLibroGuardia.verificar_cadena(), [primera])



	def test_solo_el_jefe_corrige_y_el_bombero_no_ve_el_libro(self):

		from django.contrib.auth.models import Group

		from .models import AnotacionLibroGuardia



		guardia = get_user_model().objects.get(username="guardia")

		original = AnotacionLibroGuardia.anotar(guardia, "Sale U°6 por un 02-111", "07:10")

		self.client.login(username="guardia", password="bomberos2026")

		self.client.post(reverse("corregir_anotacion", args=[original.pk]), {"texto": "x", "motivo": "No debería poder"})

		self.assertEqual(AnotacionLibroGuardia.objects.count(), 1)

		self.client.login(username="jefe", password="bomberos2026")

		self.client.post(reverse("corregir_anotacion", args=[original.pk]),

		                 {"texto": "Sale U°6 por un 02-114", "motivo": "El código era 02-114"})

		correccion = AnotacionLibroGuardia.objects.get(corrige=original)

		self.assertEqual(correccion.texto, "Sale U°6 por un 02-114")

		pantalla = self.client.get(reverse("libro_guardia"))

		self.assertContains(pantalla, "Corrige el folio 1/")

		self.assertContains(pantalla, "Libro íntegro")

		bombero = get_user_model().objects.create_user("b9", password="x")

		bombero.groups.add(Group.objects.get(name="Bombero"))

		self.client.force_login(bombero)

		self.assertRedirects(self.client.get(reverse("libro_guardia")), reverse("menu"))



	def test_dias_anteriores_se_leen_pero_no_se_anota(self):

		from datetime import timedelta

		from django.utils import timezone

		from .models import AnotacionLibroGuardia



		self.client.login(username="guardia", password="bomberos2026")

		ayer = (timezone.localdate() - timedelta(days=1)).isoformat()

		self.client.post(reverse("libro_guardia") + f"?fecha={ayer}", {"hora": "06:00", "texto": "Tarde"})

		self.assertFalse(AnotacionLibroGuardia.objects.exists())

		self.assertNotContains(self.client.get(reverse("libro_guardia"), {"fecha": ayer}), 'name="texto"')


class ConstanciaIntervencionTests(TestCase):
	"""Constancia de intervención con membrete, armada desde el parte (modelos Ceballos, Soria, Medina)."""

	def setUp(self):
		from io import StringIO
		from django.core.management import call_command

		call_command("cargar_catalogos", "--demo", stdout=StringIO())

	def test_texto_armado_desde_el_parte_y_permisos(self):
		from .models import Damnificado, ParteSiniestro

		from .models import EstadoParte
		parte = ParteSiniestro.objects.filter(damnificados__isnull=False).distinct().first()
		url = reverse("constancia_intervencion", args=[parte.pk])
		self.client.login(username="estadistica", password="bomberos2026")
		abierto = EstadoParte.objects.get(nombre_estado="Iniciado")
		ParteSiniestro.objects.filter(pk=parte.pk).update(estado_parte=abierto)
		self.assertRedirects(self.client.get(url), reverse("detalle_parte", args=[parte.pk]))  # abierto: no
		self.assertNotContains(self.client.get(reverse("detalle_parte", args=[parte.pk])), url)
		cerrado, _ = EstadoParte.objects.get_or_create(nombre_estado="Cerrado")
		ParteSiniestro.objects.filter(pk=parte.pk).update(estado_parte=cerrado)
		hoja = self.client.get(url)
		self.assertContains(hoja, "CONSTANCIA DE INTERVENCIÓN")
		self.assertContains(hoja, "HOMBRES SIN HORARIOS, NI HONORARIOS")
		self.assertContains(hoja, parte.ubicacion)
		herido = Damnificado.objects.filter(parte=parte).exclude(lesion="").first()
		self.assertContains(hoja, herido.apellido_nombre)
		self.assertContains(hoja, "escudo.jpg")
		self.assertContains(self.client.get(reverse("detalle_parte", args=[parte.pk])), url)
		self.client.login(username="guardia", password="bomberos2026")  # el guardia ya no la emite
		self.assertNotEqual(self.client.get(url).status_code, 200)
		self.assertNotContains(self.client.get(reverse("detalle_parte", args=[parte.pk])), url)
		bombero = get_user_model().objects.create_user("b8", password="x")
		self.client.force_login(bombero)
		self.assertNotEqual(self.client.get(url).status_code, 200)


class LoginConEmailTests(TestCase):
	def test_entra_con_email_o_usuario(self):
		get_user_model().objects.create_user("perez", email="perez@cuartel.com", password="clave-segura-123")
		self.assertTrue(self.client.login(username="Perez@Cuartel.com", password="clave-segura-123"))
		self.assertTrue(self.client.login(username="perez", password="clave-segura-123"))
		self.assertFalse(self.client.login(username="perez@cuartel.com", password="otra"))





class OrdenesEntregaTests(TestCase):

	def setUp(self):

		from io import StringIO

		from django.core.management import call_command

		call_command("cargar_catalogos", "--demo", stdout=StringIO())



	def test_alta_con_elementos_impresion_y_devolucion(self):

		from .models import OrdenEntrega

		self.client.login(username="guardia", password="bomberos2026")

		datos = {"fecha": "2026-11-06", "movimiento": "salida", "calidad": "prestamo", "destino": "Escuela N° 12",

		         "retira_nombre": "Paz, Juan", "retira_dni": "30.111.222",

		         "elementos-TOTAL_FORMS": 4, "elementos-INITIAL_FORMS": 0,

		         "elementos-0-cantidad": 2, "elementos-0-descripcion": "Extintor 5 kg", "elementos-0-estado": "Bueno",

		         "elementos-1-cantidad": 1, "elementos-1-descripcion": "", "elementos-2-cantidad": 1, "elementos-3-cantidad": 1}

		respuesta = self.client.post(reverse("nueva_orden"), datos)

		orden = OrdenEntrega.objects.get()

		self.assertRedirects(respuesta, reverse("detalle_orden", args=[orden.pk]))

		self.assertEqual((orden.numero_correlativo, orden.retira_dni, orden.elementos.count()), (1, "30111222", 1))

		self.assertEqual(orden.estado, "Pendiente de devolución")

		hoja = self.client.get(reverse("detalle_orden", args=[orden.pk]))

		self.assertContains(hoja, "ORDEN DE ENTREGA")

		self.assertContains(hoja, "Extintor 5 kg")

		OrdenEntrega.objects.filter(pk=orden.pk).update(fecha_devolucion="2026-11-20")

		self.assertContains(self.client.get(reverse("ordenes_entrega"), {"estado": "Devuelto"}), "00000001")

		bombero = get_user_model().objects.create_user("b7", password="x")

		self.client.force_login(bombero)

		self.assertNotEqual(self.client.get(reverse("ordenes_entrega")).status_code, 200)


class ResumenPrensaTests(TestCase):
	def setUp(self):
		from io import StringIO
		from django.core.management import call_command
		call_command("cargar_catalogos", "--demo", stdout=StringIO())

	def test_formato_sin_datos_personales(self):
		from .models import Damnificado, VehiculoSiniestrado
		self.client.login(username="guardia", password="bomberos2026")
		pantalla = self.client.get(reverse("resumen_prensa"))
		texto = pantalla.context["texto"]
		self.assertIn("PARTE DE PRENSA", texto)
		self.assertIn("- HS ", texto)
		for d in Damnificado.objects.all():
			self.assertNotIn(d.apellido_nombre.upper(), texto)
			self.assertNotIn(d.apellido_nombre, texto)
			if d.dni:
				self.assertNotIn(d.dni, texto)
		for r in VehiculoSiniestrado.objects.exclude(dominio=""):
			self.assertNotIn(r.dominio.upper(), texto.upper())
		self.assertIn("FEMENINA 34 AÑOS", texto)
		self.assertRegex(texto, r"SE TRASLADA A .+ FEMENINA CON FRACTURA DE BRAZO")
		bombero = get_user_model().objects.create_user("b6", password="x")
		self.client.force_login(bombero)
		self.assertNotEqual(self.client.get(reverse("resumen_prensa")).status_code, 200)
