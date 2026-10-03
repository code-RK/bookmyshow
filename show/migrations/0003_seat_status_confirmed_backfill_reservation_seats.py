from django.db import migrations, models


def forwards(apps, schema_editor):
    Tbl_Seat = apps.get_model('show', 'Tbl_Seat')
    Tbl_Reservation_Seat = apps.get_model('show', 'Tbl_Reservation_Seat')

    Tbl_Seat.objects.filter(status='booked').update(status='confirmed')

    # Reservations made before the reserve view wrote tbl_reservation_seat
    # only exist through tbl_seat.current_reservation; record them so their
    # seats survive a cancel.
    Tbl_Reservation_Seat.objects.bulk_create(
        [
            Tbl_Reservation_Seat(reservation_id=reservation_id, seat_id=seat_id)
            for seat_id, reservation_id in (
                Tbl_Seat.objects
                .filter(current_reservation__isnull=False)
                .values_list('id', 'current_reservation_id')
            )
        ],
        ignore_conflicts=True,
    )


def backwards(apps, schema_editor):
    Tbl_Seat = apps.get_model('show', 'Tbl_Seat')
    Tbl_Seat.objects.filter(status='confirmed').update(status='booked')


class Migration(migrations.Migration):

    dependencies = [
        ('show', '0002_alter_tbl_reservation_status_alter_tbl_seat_status_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tbl_seat',
            name='status',
            field=models.CharField(choices=[('available', 'Available'), ('confirmed', 'Confirmed')], default='available', max_length=20),
        ),
        migrations.RunPython(forwards, backwards),
    ]
